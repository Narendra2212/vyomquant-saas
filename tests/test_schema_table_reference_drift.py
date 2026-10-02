"""
tests/test_schema_table_reference_drift.py

THE DEFECT THIS FILE DETECTS
----------------------------
Three relations that shipped, mounted code reads and writes did not exist in the
production database: ``copilot_sessions``, ``copilot_messages`` and ``waitlist``.

They were not forgotten. They were declared in the WRONG PLACE — the Alembic
revision ``backend_app/alembic/versions/e88f9911b5a2_consolidate_full_schema.py``
— and Alembic is vestigial in this repository:

* production's ``alembic_version`` holds one row, ``d97ffff9c3bb``, so
  ``e88f9911b5a2`` and the three revisions after it were never applied;
* no workflow, Dockerfile or script runs ``alembic upgrade`` anywhere — the
  authoritative schema is the numbered SQL in ``backend_app/migrations/`` and
  ``migrations/``, applied by hand through ``scripts/apply_migrations.py``;
* and the revision could not be applied now even deliberately: it begins by
  creating ``library_strategies``, which already exists in production, so it
  would abort on a ``duplicate_table`` before reaching the copilot tables.

A table declared only in Alembic therefore looks declared to a reader and is
absent to a trader. That is the drift this file exists to make loud, and
``backend_app/migrations/018_copilot_and_waitlist_tables.sql`` is the fix.

WHAT THE THREE MISSING TABLES WERE ACTUALLY DOING
-------------------------------------------------
``waitlist`` was a LIVE break. ``algo22-terminal/src/lib/waitlistApi.js`` queries
``supabase.from('waitlist')`` from the browser over PostgREST,
``components/admin/AdminDashboard.jsx`` calls its admin half, and ``App.jsx``
line 634 routes ``/admin/waitlist`` to that dashboard behind ``AdminGuard`` — a
mounted surface erroring on a nonexistent relation.

The copilot tables were a SILENT break, which is why they survived.
``backend_app/main.py`` line 660 mounts the copilot router; ``routers/copilot.py``
touches the two tables nine times; every write sits inside a ``try/except`` that
only logs a warning and ``list_copilot_sessions`` returns ``[]`` on error. So the
chat streamed normally, no 500 was ever raised, and nothing was ever persisted.
Session history was empty by construction.

AND ONE LATENT DEFECT IN THE ARCHIVED ORIGINAL
----------------------------------------------
``archived_migrations/terminal_supabase_migrations/20260622000001_create_waitlist.sql``
guards the admin read with ``USING (auth.jwt() ->> 'role' = 'admin')``. That can
never be true: Supabase's top-level ``role`` claim is ``anon``,
``authenticated`` or ``service_role``. The admin role lives at
``app_metadata.role``, which is where the frontend reads it from (``App.jsx``
lines 329-331: ``user.app_metadata?.role === 'admin'``). Had that file ever been
applied, ``/admin/waitlist`` would have authenticated and then read zero rows.
:func:`TestMigration018FixesTheAdminClaimPath` pins 018's corrected predicate so
the bare form cannot come back.

WHAT EACH CLASS ASSERTS
-----------------------
``TestEveryReferencedTableIsDeclared``
    The general guard, and the one that would have caught all three tables had it
    existed: every table named as a literal in ``.table("x")`` / ``.from_("x")``
    anywhere under ``backend_app/`` is declared by a ``CREATE TABLE`` somewhere in
    the migration set. The declared set is PARSED, never listed — a hardcoded
    roster would go stale the first time a migration landed.

``TestMigration018DeclaresTheThreeTables``
    The three tables are declared by 018 **specifically**, not merely somewhere.
    Fails before 018 exists and passes after, which is what makes deleting 018 a
    red suite rather than a quiet regression.

``TestMigration018FixesTheAdminClaimPath``
    018's waitlist admin policies use the ``app_metadata`` claim path and the bare
    ``auth.jwt() ->> 'role'`` form appears in no executable statement in the file.

``TestHealthCheckProbesARealTable``
    ``SupabaseConnection.health_check`` reads a relation some migration declares.
    It used to read ``users``; there is no ``public.users`` in this schema
    (Supabase keeps users in ``auth.users``, which PostgREST does not expose), so
    PostgREST answered ``42P01``, the bare ``except`` swallowed it, and the method
    returned ``False`` *whenever the client was configured* — it could only ever
    return ``False``. Nothing calls it, including ``routers/health.py``, which is
    why that was never noticed.

``TestAlembicDagHasOneHead``
    ``d97ffff9c3bb`` forks into ``e88f9911b5a2 -> 6f1b3d9c8a7e`` and
    ``add_foreign_keys -> implement_rls_policies``, so the DAG had TWO heads and
    ``alembic upgrade head`` refused to run at all with "Multiple head revisions
    are present". ``merge_heads`` resolves it as a merge revision with an empty
    upgrade, which is the only correct resolution: deleting a revision would
    rewrite history that ``e88f9911b5a2`` is already a stamped ancestor of.

``TestWaitlistInsertPathTracksTheForm``
    ``components/waitlist/WaitlistForm.jsx`` is imported by nothing outside tests
    today, so 018 deliberately ships NO ``anon`` INSERT policy for ``waitlist`` —
    an open insert path with no form behind it is spam surface with no consumer.
    The moment the form is imported by a non-test module under
    ``algo22-terminal/src/``, this class demands the policy and fails until it is
    added, so re-mounting the form cannot silently drop every lead into a denied
    insert.

NO NETWORK, NO DATABASE, NOTHING MOCKED
---------------------------------------
Every assertion here is a parse of files in the checkout — the same oracle style
as ``tests/test_backtest_version_label_regression.py`` and
``tests/test_creator_analytics_regression.py``. There is no PostgreSQL in CI and
this file does not want one: the claim is about what the repository DECLARES,
which is exactly the thing that disagreed with production.

SQL comments are blanked before any claim is read off a migration, so 018's own
header — which quotes the broken ``auth.jwt() ->> 'role'`` predicate, the
``CREATE POLICY waitlist_public_insert`` it deliberately omits, and every table
name in play — cannot satisfy or trip a single assertion.
"""

import ast
import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

REPO_ROOT = Path(__file__).resolve().parents[1]

# --------------------------------------------------------------------------
# Named constants, each sourced from a specific file in the checkout
# --------------------------------------------------------------------------

#: The migration this finding adds. Not 017 - that file belongs to the
#: parallel entitlements workstream.
MIGRATION_018_NAME = "018_copilot_and_waitlist_tables.sql"
MIGRATION_018 = REPO_ROOT / "backend_app" / "migrations" / MIGRATION_018_NAME

#: The three relations 018 exists to declare. Verified absent from production
#: before 018 was applied, and present after (68 -> 71 tables in `public`).
TABLES_018 = ("copilot_sessions", "copilot_messages", "waitlist")

#: The claim path that actually carries the admin role, per
#: algo22-terminal/src/App.jsx lines 329-331 (`user.app_metadata?.role`).
ADMIN_CLAIM_PATH = re.compile(
    r"auth\.jwt\(\)\s*->\s*'app_metadata'\s*->>\s*'role'", re.IGNORECASE
)

#: The archived file's predicate, which can never be true: Supabase's
#: top-level `role` claim is anon / authenticated / service_role.
BROKEN_ADMIN_CLAIM = re.compile(r"auth\.jwt\(\)\s*->>\s*'role'", re.IGNORECASE)

#: The relation SupabaseConnection.health_check probes, and the migration that
#: declares it. Replaces "users", which no migration declares and which
#: production does not have.
HEALTH_PROBE_TABLE = "strategy_versions"
HEALTH_PROBE_DECLARED_BY = "001_strategy_architecture.sql"

#: Tables this application reads that NO migration in this repository
#: declares, because they predate the numbered migration set - they were
#: created in the Supabase project directly, before migrations lived here.
#: Both are CONFIRMED PRESENT in the production `public` schema, which is the
#: only reason they are tolerated: the guard below exists to catch a reference
#: to a table that is ABSENT from production, and these two are not.
#:
#: `users` is deliberately NOT in this set. It was referenced and it does not
#: exist, which is precisely the defect TestHealthCheckProbesARealTable pins.
#: Adding a name here to silence a failure re-creates the defect - add the
#: CREATE TABLE instead.
PRE_MIGRATION_BASE_TABLES = frozenset({"profiles", "strategies"})

#: The unmounted public signup form, and the module that owns its insert.
WAITLIST_FORM = REPO_ROOT / "algo22-terminal" / "src" / "components" / "waitlist" / "WaitlistForm.jsx"
WAITLIST_API = REPO_ROOT / "algo22-terminal" / "src" / "lib" / "waitlistApi.js"
TERMINAL_SRC = REPO_ROOT / "algo22-terminal" / "src"

#: The two heads `d97ffff9c3bb` forked into, and the merge that joins them.
ALEMBIC_VERSIONS = REPO_ROOT / "backend_app" / "alembic" / "versions"
EXPECTED_MERGED_HEADS = frozenset({"6f1b3d9c8a7e", "implement_rls_policies"})

# --------------------------------------------------------------------------
# Parsing helpers
# --------------------------------------------------------------------------

_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)

_CREATE_TABLE = re.compile(
    r"CREATE\s+(?:UNLOGGED\s+|TEMP(?:ORARY)?\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"(?:\"?public\"?\s*\.\s*)?\"?([A-Za-z_][A-Za-z0-9_]*)\"?",
    re.IGNORECASE,
)
_CREATE_VIEW = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:MATERIALIZED\s+)?VIEW\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"(?:\"?public\"?\s*\.\s*)?\"?([A-Za-z_][A-Za-z0-9_]*)\"?",
    re.IGNORECASE,
)

#: `.table("x")` / `.from_("x")` with a STRING LITERAL argument. Deliberately a
#: text scan rather than an AST walk, so a docstring example counts too: the
#: `client.table("users")` example in `get_client()` is how the nonexistent
#: table propagated in the first place.
_TABLE_REFERENCE = re.compile(
    r"\.(?:table|from_)\(\s*[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']\s*\)"
)


def _strip_sql_comments(text: str) -> str:
    """Blank every SQL comment, preserving line numbering.

    Run before any claim is read off a migration. 018's header quotes the broken
    predicate, the omitted INSERT policy and every table name in play; without
    this, its prose could satisfy an assertion about its executable SQL.
    """
    text = _BLOCK_COMMENT.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)
    return _LINE_COMMENT.sub("", text)


def _sql_migration_files():
    """Every hand-applied SQL migration, in both numbered directories."""
    files = []
    for directory in (REPO_ROOT / "backend_app" / "migrations", REPO_ROOT / "migrations"):
        if directory.is_dir():
            files.extend(sorted(directory.glob("*.sql")))
    return files


def _alembic_version_files():
    return sorted(
        p for p in ALEMBIC_VERSIONS.glob("*.py") if p.name != "__init__.py"
    )


def _tables_declared_by_sql(path: Path):
    """Relation names a single .sql file creates, from EXECUTABLE SQL only."""
    body = _strip_sql_comments(path.read_text(encoding="utf-8", errors="replace"))
    names = {m.group(1).lower() for m in _CREATE_TABLE.finditer(body)}
    names |= {m.group(1).lower() for m in _CREATE_VIEW.finditer(body)}
    return names


def _tables_declared_by_alembic(path: Path):
    """Relation names a single Alembic revision creates, via AST.

    AST rather than regex here because `op.create_table(` is multi-line and its
    first argument is reliably a literal, so there is nothing to guess.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:  # pragma: no cover - a revision that will not parse
        return set()
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "create_table"):
            continue
        if node.args and isinstance(node.args[0], ast.Constant) and isinstance(
            node.args[0].value, str
        ):
            names.add(node.args[0].value.lower())
    return names


def _declared_tables():
    """Map relation name -> set of files that declare it. Parsed, never listed."""
    declared: dict = {}
    for path in _sql_migration_files():
        for name in _tables_declared_by_sql(path):
            declared.setdefault(name, set()).add(path.name)
    for path in _alembic_version_files():
        for name in _tables_declared_by_alembic(path):
            declared.setdefault(name, set()).add(path.name)
    return declared


def _table_references():
    """Map relation name -> [(repo-relative path, 1-based line), ...]."""
    references: dict = {}
    for path in sorted((REPO_ROOT / "backend_app").rglob("*.py")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:  # pragma: no cover
            continue
        relative = path.relative_to(REPO_ROOT).as_posix()
        for lineno, line in enumerate(text.splitlines(), start=1):
            for match in _TABLE_REFERENCE.finditer(line):
                references.setdefault(match.group(1).lower(), []).append(
                    (relative, lineno)
                )
    return references


def _alembic_dag():
    """Map revision id -> down_revision, parsed out of the version files.

    Read with AST instead of importing Alembic: the DAG is a property of the
    files, the assertion must hold with no database and no config, and a merge's
    `down_revision` tuple is visible here exactly as it is written.
    """
    dag = {}
    for path in _alembic_version_files():
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        revision = None
        down = "__unset__"
        for node in tree.body:
            targets = []
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                targets = [node.target.id]
                value = node.value
            elif isinstance(node, ast.Assign):
                targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
                value = node.value
            else:
                continue
            if "revision" in targets:
                revision = ast.literal_eval(value)
            elif "down_revision" in targets:
                down = ast.literal_eval(value)
        if revision is not None:
            dag[revision] = (down, path.name)
    return dag


def _alembic_heads(dag):
    """Revisions nothing else descends from."""
    referenced = set()
    for down, _file in dag.values():
        if down is None or down == "__unset__":
            continue
        if isinstance(down, (tuple, list, set)):
            referenced.update(down)
        else:
            referenced.add(down)
    return sorted(rev for rev in dag if rev not in referenced)


def _executable_018():
    assert MIGRATION_018.is_file(), (
        f"{MIGRATION_018_NAME} is missing from backend_app/migrations/. It is the "
        f"declaration of {', '.join(TABLES_018)}; without it those three tables "
        "exist only in an Alembic revision that production never applied."
    )
    return _strip_sql_comments(MIGRATION_018.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# The premise, parsed rather than remembered
# --------------------------------------------------------------------------


class TestThePremiseIsReal:
    """Without these, the rest of the file could pass while asserting nothing."""

    def test_alembic_is_the_only_other_declaration_of_the_three_tables(self):
        """The three tables are declared by 018 and by one Alembic revision only.

        This is the shape of the defect: a declaration that reads as authoritative
        and that production never applied. If a second hand-applied SQL file ever
        declares them too, that is a divergence to reconcile, not a reassurance.
        """
        declared = _declared_tables()
        for table in TABLES_018:
            sources = declared.get(table, set())
            sql_sources = {s for s in sources if s.endswith(".sql")}
            assert sql_sources == {MIGRATION_018_NAME}, (
                f"{table} should be declared by exactly one hand-applied SQL "
                f"migration ({MIGRATION_018_NAME}); found {sorted(sql_sources)}. "
                "Two SQL declarations of one table is how the "
                "strategy_backtests.version divergence started - see 016's header."
            )
            assert "e88f9911b5a2_consolidate_full_schema.py" in sources, (
                f"{table} is no longer declared by e88f9911b5a2, so the premise "
                "this file records has changed. Re-read the header before editing."
            )

    def test_the_copilot_router_really_reads_those_tables(self):
        """The reference side of the drift, so the guard is not theoretical."""
        references = _table_references()
        for table in ("copilot_sessions", "copilot_messages"):
            sites = references.get(table, [])
            assert sites, f"no .table({table!r}) reference found under backend_app/"
            assert any(
                path.endswith("routers/copilot.py") for path, _line in sites
            ), f"{table} is no longer read by routers/copilot.py: {sites}"

    def test_the_waitlist_admin_surface_is_mounted(self):
        """``/admin/waitlist`` is routed, so the missing table was user-visible."""
        app_jsx = TERMINAL_SRC / "App.jsx"
        assert app_jsx.is_file()
        source = app_jsx.read_text(encoding="utf-8", errors="replace")
        assert "/admin/waitlist" in source, (
            "App.jsx no longer routes /admin/waitlist. The waitlist table's "
            "consumer has moved; re-read this file's header."
        )
        assert WAITLIST_API.is_file()
        api = WAITLIST_API.read_text(encoding="utf-8", errors="replace")
        assert "from('waitlist')" in api, (
            "waitlistApi.js no longer queries the waitlist relation directly."
        )


# --------------------------------------------------------------------------
# (a) the general guard
# --------------------------------------------------------------------------


class TestEveryReferencedTableIsDeclared:
    """The guard that would have caught all three missing tables."""

    def test_declared_set_is_parsed_and_non_trivial(self):
        """Guards the guard: a parse that silently found nothing passes anything."""
        declared = _declared_tables()
        assert len(declared) > 50, (
            f"only {len(declared)} table declarations parsed out of "
            f"{len(_sql_migration_files())} SQL files and "
            f"{len(_alembic_version_files())} Alembic revisions - the parser has "
            "stopped matching, so every assertion built on it is vacuous."
        )
        assert len(_table_references()) > 20, (
            "almost no .table(...) references parsed out of backend_app/ - the "
            "reference scan has stopped matching."
        )

    def test_every_referenced_table_has_a_create_table(self):
        declared = _declared_tables()
        references = _table_references()

        undeclared = {
            table: sites
            for table, sites in references.items()
            if table not in declared and table not in PRE_MIGRATION_BASE_TABLES
        }
        if undeclared:
            lines = []
            for table in sorted(undeclared):
                path, lineno = undeclared[table][0]
                extra = len(undeclared[table]) - 1
                suffix = f" (and {extra} more reference(s))" if extra else ""
                lines.append(f"  {table!r}  first referenced at {path}:{lineno}{suffix}")
            pytest.fail(
                "These tables are read by the application and declared by no "
                "migration, so they may not exist in the database at all - the "
                "exact condition that left copilot_sessions, copilot_messages and "
                "waitlist absent from production while reading as declared:\n"
                + "\n".join(lines)
                + "\n\nFix by adding the CREATE TABLE to backend_app/migrations/. "
                "Do NOT add the name to PRE_MIGRATION_BASE_TABLES - that set is "
                "only for relations confirmed to exist in production already."
            )

    def test_the_tolerated_base_tables_are_still_exactly_that(self):
        """The allowlist cannot rot: each entry must still be referenced and still
        undeclared. If a migration starts declaring one, delete it from the set
        rather than leaving a permanent hole in the guard."""
        declared = _declared_tables()
        references = _table_references()
        for table in sorted(PRE_MIGRATION_BASE_TABLES):
            assert table in references, (
                f"{table!r} is in PRE_MIGRATION_BASE_TABLES but nothing under "
                "backend_app/ references it any more - remove the entry."
            )
            assert table not in declared, (
                f"{table!r} is now declared by {sorted(declared[table])}, so the "
                "exemption is stale - remove it from PRE_MIGRATION_BASE_TABLES so "
                "the guard covers it like every other table."
            )
        assert "users" not in PRE_MIGRATION_BASE_TABLES, (
            "users must never be exempted: there is no public.users in this "
            "schema, which is the whole of the health_check defect."
        )


# --------------------------------------------------------------------------
# (b) 018 declares the three tables
# --------------------------------------------------------------------------


class TestMigration018DeclaresTheThreeTables:
    """Fails before 018 exists; passes after. Deleting 018 is a red suite."""

    @pytest.mark.parametrize("table", TABLES_018)
    def test_table_is_declared_by_018(self, table):
        declared_by_018 = _tables_declared_by_sql(MIGRATION_018) if MIGRATION_018.is_file() else set()
        assert table in declared_by_018, (
            f"{table} is not declared by {MIGRATION_018_NAME}. Its only other "
            "declaration is Alembic revision e88f9911b5a2, which production never "
            "applied and cannot apply (it creates library_strategies, which "
            "already exists), so the table would be absent from the database "
            "while every reader treats it as present."
        )

    def test_018_is_idempotent(self):
        """It is applied by hand, so a second run must be a no-op."""
        body = _executable_018()
        creates = _CREATE_TABLE.findall(body)
        assert len(creates) == len(TABLES_018), (
            f"expected {len(TABLES_018)} CREATE TABLE statements, found {len(creates)}"
        )
        guarded = re.findall(
            r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS", body, re.IGNORECASE
        )
        assert len(guarded) == len(creates), (
            "every CREATE TABLE in 018 must be IF NOT EXISTS; "
            f"{len(creates) - len(guarded)} is not."
        )
        indexes = re.findall(r"CREATE\s+INDEX", body, re.IGNORECASE)
        guarded_indexes = re.findall(
            r"CREATE\s+INDEX\s+IF\s+NOT\s+EXISTS", body, re.IGNORECASE
        )
        assert indexes and len(indexes) == len(guarded_indexes), (
            "every CREATE INDEX in 018 must be IF NOT EXISTS"
        )
        policies = re.findall(r"CREATE\s+POLICY", body, re.IGNORECASE)
        assert policies, "018 declares no policies at all"
        guards = re.findall(r"FROM\s+pg_policies", body, re.IGNORECASE)
        assert len(guards) >= len(policies), (
            "PostgreSQL has no CREATE POLICY IF NOT EXISTS, so each of 018's "
            f"{len(policies)} policies needs its own pg_policies guard; found "
            f"{len(guards)}."
        )

    def test_018_keeps_the_waitlist_constraints(self):
        """The four CHECKs and the UNIQUE on email, by name."""
        body = _executable_018()
        for constraint in (
            "chk_waitlist_exp",
            "chk_waitlist_status",
            "chk_waitlist_trader_type",
            "chk_waitlist_volume",
            "chk_copilot_message_role",
        ):
            assert constraint in body, f"018 lost the {constraint} constraint"
        assert re.search(r"email\s+TEXT\s+NOT\s+NULL\s+UNIQUE", body, re.IGNORECASE), (
            "waitlist.email must stay UNIQUE - waitlistApi.submit() relies on the "
            "duplicate-email rejection."
        )

    def test_018_carries_the_columns_the_form_actually_sends(self):
        """The archived original lacked trader_type and monthly_volume."""
        body = _executable_018()
        for column in ("trader_type", "monthly_volume"):
            assert re.search(rf"^\s*{column}\s+TEXT", body, re.IGNORECASE | re.MULTILINE), (
                f"waitlist.{column} is missing from 018, and waitlistApi.submit() "
                "sends it - PostgREST would answer 42703 on every signup. That is "
                "the omission the archived 20260622000001_create_waitlist.sql had."
            )

    def test_018_enables_rls_on_all_three_tables(self):
        body = _executable_018()
        for table in TABLES_018:
            assert re.search(
                rf"ALTER\s+TABLE\s+(?:public\.)?{table}\s+ENABLE\s+ROW\s+LEVEL\s+SECURITY",
                body,
                re.IGNORECASE,
            ), f"018 does not enable row level security on {table}"


# --------------------------------------------------------------------------
# (c) the admin claim path
# --------------------------------------------------------------------------


class TestMigration018FixesTheAdminClaimPath:
    """Pins the fix for the archived file's unsatisfiable admin predicate."""

    def _waitlist_policies(self):
        body = _executable_018()
        return re.findall(
            r"CREATE\s+POLICY\s+waitlist_admin_\w+.*?;", body, re.IGNORECASE | re.DOTALL
        )

    def test_the_archived_original_really_had_the_broken_predicate(self):
        """The premise. If the archived file is ever corrected or deleted, this
        says so rather than letting the next test assert against nothing."""
        archived = (
            REPO_ROOT
            / "archived_migrations"
            / "terminal_supabase_migrations"
            / "20260622000001_create_waitlist.sql"
        )
        if not archived.is_file():
            pytest.skip("the archived waitlist migration is no longer in the tree")
        body = _strip_sql_comments(archived.read_text(encoding="utf-8", errors="replace"))
        assert BROKEN_ADMIN_CLAIM.search(body), (
            "the archived file no longer carries auth.jwt() ->> 'role' = 'admin'; "
            "the defect this class pins has been edited at its source."
        )

    def test_018_uses_the_app_metadata_claim_path(self):
        policies = self._waitlist_policies()
        assert len(policies) == 3, (
            "expected three waitlist_admin_* policies (select, update, delete); "
            f"found {len(policies)}"
        )
        for policy in policies:
            assert ADMIN_CLAIM_PATH.search(policy), (
                "a waitlist admin policy does not test "
                "(auth.jwt() -> 'app_metadata' ->> 'role'). That is the claim "
                "AdminGuard already trusts (App.jsx lines 329-331); any other "
                "path reads zero rows for a genuine admin:\n" + policy
            )

    def test_018_never_uses_the_bare_role_claim(self):
        body = _executable_018()
        match = BROKEN_ADMIN_CLAIM.search(body)
        assert match is None, (
            "018 contains the bare auth.jwt() ->> 'role' predicate at offset "
            f"{match.start() if match else -1}. Supabase's top-level role claim is "
            "anon / authenticated / service_role and is NEVER 'admin', so that "
            "predicate is unsatisfiable: /admin/waitlist would authenticate and "
            "then read an empty table. Use "
            "(auth.jwt() -> 'app_metadata' ->> 'role') = 'admin'."
        )

    def test_018_does_not_reproduce_the_uid_equals_primary_key_policy(self):
        """``auth.uid()::text = id::text`` compared a caller to a row's own PK."""
        body = _executable_018()
        assert not re.search(
            r"auth\.uid\(\)\s*::\s*text\s*=\s*id\s*::\s*text", body, re.IGNORECASE
        ), (
            "018 reproduces the archived file's 'read own entry' policy, which "
            "compares the caller's user id to the waitlist row's primary key. Two "
            "unrelated uuids are never equal; the policy grants nothing."
        )


# --------------------------------------------------------------------------
# (d) health_check probes something real
# --------------------------------------------------------------------------


def _health_check_table_references():
    """Literal relation names reached inside ``SupabaseConnection.health_check``.

    Structural on purpose: a regex over the whole module would also see the
    ``get_client()`` docstring example and the prose in this method's own
    docstring, so it could neither localise the defect nor prove it was fixed.
    """
    path = REPO_ROOT / "backend_app" / "core" / "supabase_connection.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for cls in ast.walk(tree):
        if not (isinstance(cls, ast.ClassDef) and cls.name == "SupabaseConnection"):
            continue
        for fn in cls.body:
            if not (
                isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
                and fn.name == "health_check"
            ):
                continue
            for node in ast.walk(fn):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not (
                    isinstance(func, ast.Attribute) and func.attr in ("table", "from_")
                ):
                    continue
                if not node.args:
                    continue
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    found.append(arg.value.lower())
    return found


class TestHealthCheckProbesARealTable:
    """``health_check`` read ``users``, which does not exist, so it could only
    ever return ``False`` once the client was configured."""

    def test_health_check_reaches_exactly_one_table(self):
        tables = _health_check_table_references()
        assert len(tables) == 1, (
            "SupabaseConnection.health_check should probe exactly one relation by "
            f"literal name; found {tables}. A probe with no literal cannot be "
            "checked against the migration set at all."
        )

    def test_health_check_table_is_declared_by_a_migration(self):
        declared = _declared_tables()
        for table in _health_check_table_references():
            assert table in declared, (
                f"SupabaseConnection.health_check probes {table!r}, which no "
                "migration declares. That is how this method came to return False "
                "unconditionally: it read 'users', PostgREST answered 42P01 "
                "because Supabase keeps users in auth.users, and the bare except "
                "turned the error into a health answer of False."
            )

    def test_health_check_does_not_probe_users(self):
        assert "users" not in _health_check_table_references(), (
            "health_check is back on 'users'. There is no public.users in this "
            "schema, so this method would return False whenever the client is "
            "configured - the one answer a health check must not give "
            "unconditionally."
        )

    def test_the_probe_table_is_the_recorded_one(self):
        """Pins the choice, and the migration it is justified against."""
        assert _health_check_table_references() == [HEALTH_PROBE_TABLE]
        declared = _declared_tables()
        assert HEALTH_PROBE_DECLARED_BY in declared[HEALTH_PROBE_TABLE], (
            f"{HEALTH_PROBE_TABLE} is no longer declared by "
            f"{HEALTH_PROBE_DECLARED_BY}; re-justify the probe table."
        )

    def test_the_docstring_example_does_not_teach_the_same_mistake(self):
        """``get_client()``'s example said ``client.table("users")``."""
        declared = _declared_tables()
        path = REPO_ROOT / "backend_app" / "core" / "supabase_connection.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(
                node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                continue
            doc = ast.get_docstring(node)
            if not doc:
                continue
            for match in _TABLE_REFERENCE.finditer(doc):
                name = match.group(1).lower()
                if name not in declared and name not in PRE_MIGRATION_BASE_TABLES:
                    offenders.append((getattr(node, "name", "<module>"), name))
        assert not offenders, (
            "a docstring in supabase_connection.py shows .table(...) on a relation "
            f"no migration declares: {offenders}. A worked example is copied; this "
            "one is how 'users' spread."
        )


# --------------------------------------------------------------------------
# (e) one Alembic head
# --------------------------------------------------------------------------


class TestAlembicDagHasOneHead:
    """Two heads made ``alembic upgrade head`` unrunnable, not merely untidy."""

    def test_the_fork_is_still_where_it_was_recorded(self):
        """The premise: ``d97ffff9c3bb`` is production's stamp and the fork point."""
        dag = _alembic_dag()
        assert "d97ffff9c3bb" in dag, "the revision production is stamped at is gone"
        children = sorted(
            rev
            for rev, (down, _f) in dag.items()
            if down == "d97ffff9c3bb"
            or (isinstance(down, (tuple, list)) and "d97ffff9c3bb" in down)
        )
        assert children == ["add_foreign_keys", "e88f9911b5a2"], (
            f"the fork below d97ffff9c3bb has changed: {children}. Re-read this "
            "class before editing it."
        )

    def test_exactly_one_head(self):
        dag = _alembic_dag()
        heads = _alembic_heads(dag)
        assert len(heads) == 1, (
            f"the Alembic DAG has {len(heads)} heads: {heads}. With more than one, "
            "`alembic upgrade head` refuses to run at all - 'Multiple head "
            "revisions are present'. Resolve it with a merge revision whose "
            "down_revision is the tuple of the heads; do NOT delete a revision, "
            "because e88f9911b5a2 and add_foreign_keys both descend from the "
            "revision production is stamped at."
        )

    def test_the_merge_revision_joins_both_recorded_heads(self):
        dag = _alembic_dag()
        merges = {
            rev: down
            for rev, (down, _f) in dag.items()
            if isinstance(down, (tuple, list)) and len(down) > 1
        }
        assert merges, "no merge revision found in backend_app/alembic/versions/"
        assert any(
            frozenset(down) == EXPECTED_MERGED_HEADS for down in merges.values()
        ), (
            "no merge revision joins the two recorded heads "
            f"{sorted(EXPECTED_MERGED_HEADS)}; merges found: {merges}"
        )

    def test_the_merge_revision_changes_nothing(self):
        """A merge that migrates is a merge that can fail. This one must not."""
        dag = _alembic_dag()
        merge_files = [
            filename
            for _rev, (down, filename) in dag.items()
            if isinstance(down, (tuple, list)) and frozenset(down) == EXPECTED_MERGED_HEADS
        ]
        assert merge_files, "the merge revision file could not be located"
        for filename in merge_files:
            tree = ast.parse((ALEMBIC_VERSIONS / filename).read_text(encoding="utf-8"))
            for fn in tree.body:
                if isinstance(fn, ast.FunctionDef) and fn.name in ("upgrade", "downgrade"):
                    statements = [
                        node
                        for node in fn.body
                        if not (
                            isinstance(node, ast.Pass)
                            or (
                                isinstance(node, ast.Expr)
                                and isinstance(node.value, ast.Constant)
                            )
                        )
                    ]
                    assert not statements, (
                        f"{filename}:{fn.name}() does work. A merge revision exists "
                        "to join two lineages; any DDL in it would run against a "
                        "database whose state neither branch described."
                    )


# --------------------------------------------------------------------------
# (f) the waitlist insert path tracks the form
# --------------------------------------------------------------------------


def _modules_importing_waitlist_form():
    """Non-test modules under ``algo22-terminal/src/`` that import the form."""
    importers = []
    pattern = re.compile(
        r"""(?:import[^;\n]*\bfrom\s*['"][^'"]*WaitlistForm['"]"""
        r"""|import\s*\(\s*['"][^'"]*WaitlistForm['"])"""
    )
    for path in sorted(TERMINAL_SRC.rglob("*.js*")):
        relative = path.relative_to(REPO_ROOT).as_posix()
        lowered = relative.lower()
        if ".test." in lowered or ".spec." in lowered or "/tests/" in lowered or "/__tests__/" in lowered:
            continue
        if path == WAITLIST_FORM:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if pattern.search(text):
            importers.append(relative)
    return importers


class TestWaitlistInsertPathTracksTheForm:
    """Re-mounting the public form without opening the insert path must fail
    loudly, not drop every lead into a denied insert."""

    def test_the_form_still_needs_an_insert_to_work(self):
        """The premise, so the implication below has teeth."""
        assert WAITLIST_FORM.is_file(), (
            "WaitlistForm.jsx is gone. If the public signup funnel has been "
            "removed for good, delete this class and 018's section 4d with it."
        )
        form = WAITLIST_FORM.read_text(encoding="utf-8", errors="replace")
        assert "waitlistApi" in form, "WaitlistForm.jsx no longer uses waitlistApi"
        api = WAITLIST_API.read_text(encoding="utf-8", errors="replace")
        assert re.search(r"from\(\s*'waitlist'\s*\)\s*\.insert", api, re.DOTALL), (
            "waitlistApi.submit() no longer inserts into the waitlist relation, so "
            "the insert-policy requirement below no longer follows."
        )

    def test_a_mounted_form_requires_an_anon_insert_policy(self):
        importers = _modules_importing_waitlist_form()
        body = _executable_018()
        has_insert_policy = bool(
            re.search(
                r"CREATE\s+POLICY\s+\w+\s+ON\s+(?:public\.)?waitlist\s+FOR\s+INSERT",
                body,
                re.IGNORECASE,
            )
        )
        if importers:
            assert has_insert_policy, (
                "WaitlistForm.jsx is now imported by "
                f"{importers}, so the public signup form is MOUNTED - but "
                f"{MIGRATION_018_NAME} still has no anon INSERT policy on "
                "waitlist. RLS denies by default, so every submission would be "
                "rejected and every lead silently lost. Add, in this same change:\n"
                "    CREATE POLICY waitlist_public_insert ON public.waitlist\n"
                "        FOR INSERT TO anon, authenticated WITH CHECK (true);\n"
                "    GRANT INSERT ON public.waitlist TO anon, authenticated;\n"
                "(guarded on pg_policies, like every other policy in 018), and put "
                "rate limiting in front of it."
            )
        else:
            assert not has_insert_policy, (
                f"{MIGRATION_018_NAME} opens an anon INSERT path on waitlist while "
                "WaitlistForm.jsx is imported by nothing outside tests. A "
                "world-writable production table with no form behind it is spam "
                "surface with no consumer. Mount the form in this same change, or "
                "drop the policy."
            )

    def test_the_absence_is_recorded_in_the_migration(self):
        """While the form is unmounted, 018 must say why the policy is missing -
        otherwise the next reader adds it back by reflex."""
        _executable_018()  # asserts the file is present, with a readable message
        if _modules_importing_waitlist_form():
            pytest.skip("the form is mounted; the insert policy is required instead")
        raw = MIGRATION_018.read_text(encoding="utf-8")
        assert "waitlist_public_insert" in raw, (
            "018 does not name the INSERT policy it deliberately omits. The "
            "omission has to be legible as a decision, not an oversight."
        )
        assert "unmounted" in raw.lower(), (
            "018 does not record that the public form is unmounted, which is the "
            "entire reason the insert path is closed."
        )

# --------------------------------------------------------------------------
# (g) the SQL migrations themselves
#
# THE HOLE THIS SECTION CLOSES
# ----------------------------
# Everything above scans PYTHON call sites. It says nothing about the SQL, and
# a migration can therefore read a relation that nothing declares and leave the
# suite green. One does: `migrations/referral_system_redesign.sql` backfills
# from the pre-redesign `referrals` table, which does not exist in this
# deployment - `to_regclass('public.referrals')` is NULL and the relation is
# absent from `pg_tables`. Its only declaration anywhere in the tree is the
# Alembic revision `6f1b3d9c8a7e_add_referrals.py`, which production never
# applied, exactly like the three tables this file's header is about.
#
# Unguarded, that statement aborts the file with `42P01 relation "referrals"
# does not exist`, so the migration was NOT REPLAYABLE: a staging or DR rebuild
# from the migration set died partway through the referral system. No data was
# lost and nothing broke at runtime - no Python and no frontend module reads
# `referrals`, the live system uses the redesigned `referral_*` tables - but a
# migration set that cannot be replayed is not a migration set.
#
# The fix is a `to_regclass` guard around each statement that reads it, with the
# statement preserved verbatim inside. `TestGuardedAbsentRelationsStayGuarded`
# is what stops that guard being removed again.
# --------------------------------------------------------------------------

#: Relations that SQL DML reads or writes, that NO SQL migration declares, and
#: that are CONFIRMED PRESENT in the production `public` schema. They predate
#: the numbered migration set - created in the Supabase project directly, or
#: (library_strategies) declared only in Alembic - so no CREATE TABLE for them
#: exists here to find. Row counts are the ones observed in production when
#: this guard was written, and are recorded as the evidence for the exemption.
#:
#: Adding a name here to silence a failure re-creates the defect. The name
#: belongs here ONLY if the relation is already in the database; otherwise add
#: the CREATE TABLE.
SQL_UNDECLARED_PRESENT_IN_PRODUCTION = {
    "profiles": "pre-migration base table, 180 rows in production",
    "strategies": "pre-migration base table, 187 rows in production",
    "execution_records": "pre-migration base table, 198 rows; 006 only ALTERs it",
    "library_strategies": "present (0 rows); declared only by Alembic e88f9911b5a2",
}

#: Relations that SQL DML reads, that NO migration declares, and that are
#: ABSENT from production. These are tolerated for ONE reason only: every
#: statement that touches them sits inside an existence guard, so the migration
#: skips the statement instead of aborting on `42P01`.
#:
#: The value is (file that references it, guard form). The guard form is pinned
#: per relation because it is the thing being protected: drop the guard and the
#: file stops being replayable again.
SQL_GUARDED_ABSENT_RELATIONS = {
    "referrals": ("referral_system_redesign.sql", "to_regclass"),
    "exchanges": ("003_signal_trace_preflight.sql", "information_schema"),
}

#: The two statements in referral_system_redesign.sql that read the legacy
#: table. Pinned so that "guarded" can never be achieved by deleting them.
REFERRAL_REDESIGN = REPO_ROOT / "migrations" / "referral_system_redesign.sql"
REFERRALS_BACKFILL_TARGETS = ("referral_relationships", "referral_wallets")

_IDENT_CHAR = re.compile(r"[A-Za-z0-9_$]")

#: Words that can follow FROM / JOIN / UPDATE / INSERT INTO without naming a
#: relation. An unquoted reserved word cannot be a relation name in these
#: positions, so a match on one is noise by construction. This is a
#: keyword stoplist, NOT an allowlist of table names - no relation of this
#: application is ever named here.
_SQL_NOISE_WORDS = frozenset(
    """
    select insert update delete set values with recursive as on using where and or not
    null is in exists case when then else end from join inner left right full outer cross
    lateral only distinct all any some order group by having limit offset fetch for
    returning conflict do nothing share key no of union intersect except table into
    default true false unknown between like ilike similar asc desc nulls first last rows
    range unbounded preceding following current row grant revoke to public create alter
    drop begin commit rollback declare if elsif loop while return perform raise notice
    exception execute language immutable stable volatile security definer invoker setof
    constraint primary foreign references unique check cascade restrict replace trigger
    before after each instead function procedure policy index view materialized
    concurrently returns stdin stdout program
    """.split()
)

#: A leading word that makes the following `UPDATE` something other than
#: `UPDATE <relation>`: `FOR UPDATE`, `ON CONFLICT DO UPDATE SET`,
#: `ON UPDATE CASCADE`, `BEFORE INSERT OR UPDATE ON`, `GRANT ... UPDATE ON`.
_UPDATE_NOT_DML_LEAD = frozenset(
    {"for", "do", "on", "before", "after", "grant", "revoke", "or", "and", "select",
     "insert", "delete", "truncate", "references", "cascade", "no", "key"}
)
#: `a IS [NOT] DISTINCT FROM b` puts a COLUMN after FROM, not a relation.
_FROM_NOT_DML_LEAD = frozenset({"distinct"})
#: Functions whose argument list contains a bare `FROM` as syntax:
#: `EXTRACT(EPOCH FROM ts)`, `SUBSTRING(s FROM 2)`, `TRIM(BOTH ' ' FROM s)`.
_FROM_AS_FUNCTION_SYNTAX = frozenset(
    {"extract", "substring", "trim", "overlay", "position"}
)

_SQL_RELATION = (
    r'(?:"?(?P<schema>[A-Za-z_][A-Za-z0-9_$]*)"?\s*\.\s*)?'
    r'"?(?P<name>[A-Za-z_][A-Za-z0-9_$]*)"?'
)
_SQL_DML = re.compile(
    r"(?P<lead>\b[A-Za-z_]+\s+)?"
    r"\b(?P<kw>FROM|JOIN|INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+(?:ONLY\s+)?"
    + _SQL_RELATION,
    re.IGNORECASE,
)
_SQL_CTE = re.compile(
    r"(?:\bWITH\s+(?:RECURSIVE\s+)?|,\s*)\"?([A-Za-z_][A-Za-z0-9_$]*)\"?\s+AS\s*\(",
    re.IGNORECASE,
)
_STATEMENT_HEAD = re.compile(r"\s*([A-Za-z_]+)")
_DO_BLOCK_OPEN = re.compile(r"\bDO\s+(\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$)", re.IGNORECASE)

#: Only the schema this application owns is in scope. A qualified reference to
#: anything else is a catalog probe, another schema's object, or a PL/pgSQL
#: record pseudo-row (`OLD.status`, `NEW.id`) - none of which the migration set
#: declares or should.
_OWNED_SCHEMAS = frozenset({"", "public"})


def _blank_run(text: str) -> str:
    """Replace every character except newlines with a space.

    Length and line numbering are preserved, which is what lets the DML scan
    and the guard scan share offsets with the raw file.
    """
    return re.sub(r"[^\n]", " ", text)


def _fold_quoted_identifier(text: str) -> str:
    """``"Users can update own rows"`` -> ``"Users_can_update_own_rows"``.

    A double-quoted identifier is one token. Left alone, the keywords inside a
    quoted policy name read as DML: `CREATE POLICY "Users can update own
    notifications"` produced a reference to a relation called ``own``. Folding
    the interior to identifier characters removes the word boundaries that made
    ``update`` match, keeps the length, and leaves a genuinely quoted relation
    name such as ``"referral_codes"`` untouched.
    """
    if len(text) < 2:
        return text
    inner = "".join(
        c if c == "\n" else ("_" if not _IDENT_CHAR.match(c) else c)
        for c in text[1:-1]
    )
    return text[0] + inner + text[-1]


def _mask_sql(text: str, mask_literals: bool = True) -> str:
    """Blank what is not executable SQL, PRESERVING LENGTH exactly.

    * ``--`` and ``/* */`` comments are blanked. Prose in a migration header -
      018's, which names every table in play, and referral_system_redesign's
      new one, which names ``referrals`` fifteen times - therefore cannot
      satisfy or trip a single assertion.
    * Single-quoted literals are blanked when ``mask_literals`` is set. Without
      this, ``RAISE NOTICE 'rewriting rows from old to new'`` reports reads of
      relations called ``old`` and ``new``.
    * Double-quoted identifiers are folded to one token (see above).
    * Dollar-quote DELIMITERS are left in place and their BODIES are scanned as
      ordinary SQL, because a PL/pgSQL body carries real DML - including the
      ``EXECUTE $tag$ ... $tag$`` the referrals guard wraps its statements in,
      which is why guarding the statement does not hide it from this scan.

    ``mask_literals=False`` yields the view used to find existence guards,
    whose argument - ``to_regclass('referrals')`` - is itself a literal.
    """
    out = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if text.startswith("--", i):
            j = text.find("\n", i)
            j = n if j == -1 else j
            out.append(_blank_run(text[i:j]))
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j == -1 else j + 2
            out.append(_blank_run(text[i:j]))
            i = j
        elif ch == "'":
            j = i + 1
            while j < n:
                if text[j] == "'":
                    if j + 1 < n and text[j + 1] == "'":
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            out.append(_blank_run(text[i:j]) if mask_literals else text[i:j])
            i = j
        elif ch == '"':
            j = i + 1
            while j < n:
                if text[j] == '"':
                    if j + 1 < n and text[j + 1] == '"':
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            out.append(_fold_quoted_identifier(text[i:j]))
            i = j
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _statement_head(body: str, pos: int) -> str:
    """First word of the statement ``pos`` belongs to.

    Used for one thing: ``REVOKE ALL ON x FROM anon`` names a ROLE after FROM,
    not a relation. That single rule removed 48 false positives.
    """
    match = _STATEMENT_HEAD.match(body, body.rfind(";", 0, pos) + 1)
    return match.group(1).lower() if match else ""


def _enclosing_call(body: str, pos: int) -> str:
    """Name of the function whose argument list ``pos`` sits inside, or ``""``.

    Resolves ``EXTRACT(EPOCH FROM created_at)`` as function syntax rather than
    as a read of a relation called ``created_at``.
    """
    depth = 0
    i = pos - 1
    while i >= 0:
        ch = body[i]
        if ch == ")":
            depth += 1
        elif ch == "(":
            if depth == 0:
                j = i - 1
                while j >= 0 and body[j] in " \t\n":
                    j -= 1
                end = j + 1
                while j >= 0 and _IDENT_CHAR.match(body[j]):
                    j -= 1
                return body[j + 1:end].lower()
            depth -= 1
        elif ch == ";":
            return ""
        i -= 1
    return ""


def _dml_relations_in(masked: str):
    """Yield ``(relation, lineno, keyword, offset)`` for owned-schema DML.

    ``masked`` must come from :func:`_mask_sql`, which is why every exclusion
    below is about SQL syntax rather than about particular table names.
    """
    ctes = {m.group(1).lower() for m in _SQL_CTE.finditer(masked)}
    for match in _SQL_DML.finditer(masked):
        keyword = re.sub(r"\s+", " ", match.group("kw")).upper()
        lead = (match.group("lead") or "").strip().lower()
        if keyword == "UPDATE" and lead in _UPDATE_NOT_DML_LEAD:
            continue
        if keyword == "FROM":
            if lead in _FROM_NOT_DML_LEAD:
                continue
            if _statement_head(masked, match.start()) == "revoke":
                continue
            if _enclosing_call(masked, match.start("kw")) in _FROM_AS_FUNCTION_SYNTAX:
                continue
        if (match.group("schema") or "").lower() not in _OWNED_SCHEMAS:
            continue
        name = match.group("name").lower()
        # `pg_` is a reserved prefix in PostgreSQL: no object of this
        # application can be called that, so every match is a catalog probe.
        # Excluded as a CLASS, not as a list of catalog table names.
        if name in _SQL_NOISE_WORDS or name.startswith("pg_"):
            continue
        if name in ctes:
            continue
        # A `(` straight after the name makes it a call, not a relation:
        # `FROM unnest(...)`, `FROM jsonb_array_elements(...)`.
        if masked[match.end():match.end() + 1].lstrip(" \t")[:1] == "(":
            continue
        yield name, masked.count("\n", 0, match.start("kw")) + 1, keyword, match.start("kw")


def _sql_dml_references():
    """Map relation -> [(filename, lineno, keyword, offset), ...] over the
    whole hand-applied SQL migration set."""
    references: dict = {}
    for path in _sql_migration_files():
        masked = _mask_sql(path.read_text(encoding="utf-8", errors="replace"))
        for name, lineno, keyword, offset in _dml_relations_in(masked):
            references.setdefault(name, []).append(
                (path.name, lineno, keyword, offset)
            )
    return references


def _sql_declared_relations():
    """Map relation -> {filenames} from executable ``CREATE TABLE``/``VIEW``.

    Parsed off the SAME masked text the DML scan uses, so the two sides cannot
    disagree: a ``CREATE TABLE`` that only appears inside a string literal in a
    ``RAISE NOTICE`` is not a declaration, and this does not count it as one.
    Alembic is deliberately NOT consulted - a relation declared only there is
    the very drift this file exists to detect.
    """
    declared: dict = {}
    for path in _sql_migration_files():
        masked = _mask_sql(path.read_text(encoding="utf-8", errors="replace"))
        for pattern in (_CREATE_TABLE, _CREATE_VIEW):
            for match in pattern.finditer(masked):
                declared.setdefault(match.group(1).lower(), set()).add(path.name)
    return declared


def _do_block_spans(text: str):
    """``(start, end)`` of every ``DO $tag$ ... $tag$`` block."""
    spans = []
    for match in _DO_BLOCK_OPEN.finditer(text):
        delimiter = match.group(1)
        close = text.find(delimiter, match.end())
        if close == -1:
            continue
        spans.append((match.start(), close + len(delimiter)))
    return spans


def _existence_guard_pattern(relation: str, form: str):
    name = re.escape(relation)
    if form == "to_regclass":
        return re.compile(
            r"to_regclass\s*\(\s*'(?:[A-Za-z_][A-Za-z0-9_]*\s*\.\s*)?" + name + r"'\s*\)",
            re.IGNORECASE,
        )
    if form == "information_schema":
        return re.compile(r"table_name\s*=\s*'" + name + r"'", re.IGNORECASE)
    raise AssertionError("unknown guard form %r" % form)


def _guarded_sites(path: Path, relation: str, form: str):
    """Partition this file's DML sites for ``relation`` into guarded/unguarded.

    A site is GUARDED when it sits inside a ``DO`` block whose body performs the
    pinned existence test for ``relation`` at an offset BEFORE the site. The
    guard view keeps string literals, because the test itself is a literal;
    offsets line up with the DML view because :func:`_mask_sql` preserves
    length exactly.
    """
    raw = path.read_text(encoding="utf-8", errors="replace")
    masked = _mask_sql(raw)
    guard_view = _mask_sql(raw, mask_literals=False)
    pattern = _existence_guard_pattern(relation, form)
    blocks = _do_block_spans(guard_view)

    guarded, unguarded = [], []
    for name, lineno, keyword, offset in _dml_relations_in(masked):
        if name != relation:
            continue
        covered = False
        for start, end in blocks:
            if not start <= offset < end:
                continue
            guard = pattern.search(guard_view, start, end)
            if guard and guard.start() < offset:
                covered = True
                break
        (guarded if covered else unguarded).append((lineno, keyword))
    return guarded, unguarded


#: Every hazard that produced a false positive while this scanner was built,
#: plus the ones named in review that the migration set does not happen to
#: contain today (CTEs, ``COPY``, ``EXTRACT(... FROM ...)``). Five relations
#: are genuinely read or written here and nothing else is.
_SCANNER_FIXTURE = """
-- A comment: INSERT INTO ghost_in_a_line_comment SELECT * FROM ghost_comment_two;
/* A block comment: UPDATE ghost_in_a_block_comment SET x = 1;
   and DELETE FROM ghost_block_two; */
CREATE TABLE real_one (id uuid PRIMARY KEY, created_at timestamptz, status text);
CREATE TABLE real_two (id uuid, real_one_id uuid, status text);
CREATE VIEW real_view AS SELECT 1 AS x;

-- Prose inside a string literal is not SQL.
DO $$
BEGIN
    RAISE NOTICE 'copying rows FROM ghost_in_a_literal INTO ghost_literal_two';
    RAISE NOTICE 'CREATE TABLE ghost_declared_in_a_literal (id uuid)';
END $$;

-- Keywords inside a quoted identifier are not DML.
CREATE POLICY "Users can update own rows FROM anywhere" ON real_one FOR UPDATE
    USING (true);

-- Catalog and information_schema probes.
SELECT 1 FROM pg_policies WHERE tablename = 'real_one';
SELECT 1 FROM pg_tables JOIN pg_namespace ON true;
SELECT 1 FROM pg_roles WHERE rolname = 'anon';
SELECT 1 FROM pg_catalog.pg_class;
SELECT 1 FROM information_schema.columns WHERE table_name = 'real_one';

-- GRANT and REVOKE name roles, not relations.
GRANT SELECT, UPDATE ON real_one TO anon;
REVOKE ALL ON real_one FROM anon, authenticated;

-- A CTE name is not a relation.
WITH recent AS (SELECT id FROM real_one), stale AS (SELECT id FROM real_two)
INSERT INTO real_two (id) SELECT id FROM recent UNION SELECT id FROM stale;

-- Aliases, subqueries, EXISTS, set-returning functions, DISTINCT FROM, EXTRACT.
SELECT * FROM real_one r
    JOIN real_two t ON t.real_one_id = r.id
   WHERE EXISTS (SELECT 1 FROM real_view v WHERE v.x = r.id)
     AND r.id IN (SELECT y FROM unnest(ARRAY[r.id]) AS y)
     AND r.status IS DISTINCT FROM status
     AND EXTRACT(EPOCH FROM created_at) > 0;
SELECT * FROM jsonb_array_elements('[]'::jsonb);

-- ON CONFLICT DO UPDATE SET, and row locking.
INSERT INTO real_one (id) VALUES (gen_random_uuid())
ON CONFLICT (id) DO UPDATE SET created_at = NOW();
SELECT id FROM real_two FOR UPDATE;

-- Trigger timing clauses.
CREATE TRIGGER t BEFORE INSERT OR UPDATE ON real_one
    FOR EACH ROW EXECUTE FUNCTION f();

-- COPY's FROM takes a source, not a relation.
COPY real_one FROM STDIN;

-- Real DML inside a PL/pgSQL body, including through EXECUTE: these two
-- undeclared relations are what prove the scanner still FIRES.
DO $outer$
BEGIN
    UPDATE real_two SET status = 'x' WHERE id IS NOT NULL;
    DELETE FROM real_view;
    EXECUTE $inner$
        INSERT INTO ghost_target (id) SELECT id FROM ghost_source;
    $inner$;
END
$outer$;
"""

_SCANNER_FIXTURE_RELATIONS = frozenset(
    {"real_one", "real_two", "real_view", "ghost_target", "ghost_source"}
)


class TestTheSqlScannerIsHonest:
    """Guards the guard. A scanner that quietly matches nothing, or that is
    drowning in false positives someone then allowlists away, asserts nothing."""

    def test_masking_preserves_offsets(self):
        """Both views and the raw text must agree on offsets, or the
        guarded/unguarded partition is meaningless."""
        for path in _sql_migration_files():
            raw = path.read_text(encoding="utf-8", errors="replace")
            assert len(_mask_sql(raw)) == len(raw), f"{path.name}: masking changed length"
            assert len(_mask_sql(raw, mask_literals=False)) == len(raw), (
                f"{path.name}: guard view changed length"
            )
            assert _mask_sql(raw).count("\n") == raw.count("\n"), (
                f"{path.name}: masking changed the line count"
            )

    def test_the_fixture_resolves_to_exactly_its_real_relations(self):
        """Every hazard that produced a false positive while this was built.

        If a change to the scanner starts reporting ``own``, ``anon``,
        ``recent``, ``stdin``, ``created_at`` or a name out of a comment, this
        fails here rather than in a migration where the instinct is to reach
        for the exemption list.
        """
        found = {
            name for name, _l, _k, _o in _dml_relations_in(_mask_sql(_SCANNER_FIXTURE))
        }
        assert found == _SCANNER_FIXTURE_RELATIONS, (
            "scanner drift. unexpected: "
            f"{sorted(found - _SCANNER_FIXTURE_RELATIONS)}; missing: "
            f"{sorted(_SCANNER_FIXTURE_RELATIONS - found)}"
        )

    def test_the_fixture_declares_only_what_is_executable(self):
        """``CREATE TABLE`` inside a ``RAISE NOTICE`` literal is not a
        declaration."""
        masked = _mask_sql(_SCANNER_FIXTURE)
        declared = {m.group(1).lower() for m in _CREATE_TABLE.finditer(masked)}
        declared |= {m.group(1).lower() for m in _CREATE_VIEW.finditer(masked)}
        assert declared == {"real_one", "real_two", "real_view"}, declared

    def test_the_scan_over_the_real_migrations_is_non_trivial(self):
        declared = _sql_declared_relations()
        references = _sql_dml_references()
        site_count = sum(len(v) for v in references.values())
        assert len(declared) > 50, (
            f"only {len(declared)} relations parsed as declared out of "
            f"{len(_sql_migration_files())} SQL files - the declaration parser "
            "has stopped matching, so every assertion built on it is vacuous."
        )
        assert site_count > 80, (
            f"only {site_count} DML sites parsed out of the migration set - the "
            "DML scan has stopped matching."
        )
        assert len(references) > 20, (
            f"only {len(references)} distinct relations reached by DML - too few "
            "to be a real parse of this migration set."
        )

    def test_the_two_exemption_sets_do_not_overlap(self):
        """A relation is either known-present or guarded-absent, never both -
        the justifications are incompatible."""
        overlap = set(SQL_UNDECLARED_PRESENT_IN_PRODUCTION) & set(
            SQL_GUARDED_ABSENT_RELATIONS
        )
        assert not overlap, overlap
        assert "users" not in SQL_UNDECLARED_PRESENT_IN_PRODUCTION
        assert "users" not in SQL_GUARDED_ABSENT_RELATIONS


class TestEverySqlMigrationRelationIsDeclared:
    """The general SQL-side guard, one level out from
    :class:`TestEveryReferencedTableIsDeclared`.

    That class asks whether every table PYTHON reads is declared. This one asks
    whether every relation THE MIGRATIONS THEMSELVES read is declared, which is
    the question that was not being asked and under which `referrals` passed.
    """

    def test_every_relation_sql_dml_touches_is_declared(self):
        declared = _sql_declared_relations()
        references = _sql_dml_references()
        exempt = set(SQL_UNDECLARED_PRESENT_IN_PRODUCTION) | set(
            SQL_GUARDED_ABSENT_RELATIONS
        )

        undeclared = {
            name: sites
            for name, sites in references.items()
            if name not in declared and name not in exempt
        }
        if undeclared:
            lines = []
            for name in sorted(undeclared):
                filename, lineno, keyword, _offset = undeclared[name][0]
                extra = len(undeclared[name]) - 1
                suffix = f" (and {extra} more)" if extra else ""
                lines.append(
                    f"  {name!r}  {keyword} at {filename}:{lineno}{suffix}"
                )
            pytest.fail(
                "These relations are READ OR WRITTEN by a SQL migration and "
                "declared by no CREATE TABLE or CREATE VIEW in the migration "
                "set, so the migration aborts with 42P01 on any database that "
                "does not already happen to have them - which is what made "
                "referral_system_redesign.sql unreplayable:\n"
                + "\n".join(lines)
                + "\n\nFix by adding the CREATE TABLE, or - if the relation is "
                "genuinely optional legacy - by wrapping every statement that "
                "touches it in a to_regclass guard and recording it in "
                "SQL_GUARDED_ABSENT_RELATIONS with its justification."
            )

    def test_the_present_but_undeclared_set_is_still_exactly_that(self):
        """The exemption cannot rot: each entry must still be touched by SQL DML
        and still be undeclared. If a migration starts declaring one, delete the
        entry rather than leaving a permanent hole."""
        declared = _sql_declared_relations()
        references = _sql_dml_references()
        for name in sorted(SQL_UNDECLARED_PRESENT_IN_PRODUCTION):
            assert name in references, (
                f"{name!r} is in SQL_UNDECLARED_PRESENT_IN_PRODUCTION but no SQL "
                "migration touches it any more - remove the entry."
            )
            assert name not in declared, (
                f"{name!r} is now declared by {sorted(declared[name])}, so the "
                "exemption is stale - remove it so the guard covers it like "
                "every other relation."
            )

    def test_the_python_and_sql_exemptions_agree_where_they_overlap(self):
        """``profiles`` and ``strategies`` are exempt on both sides for the same
        reason. If one side's justification changes, both must be revisited."""
        for name in PRE_MIGRATION_BASE_TABLES:
            assert name in SQL_UNDECLARED_PRESENT_IN_PRODUCTION, (
                f"{name!r} is exempt for the Python guard but not the SQL guard. "
                "The two sets record the same fact - that the relation predates "
                "the migration set - and must not drift apart."
            )


class TestGuardedAbsentRelationsStayGuarded:
    """The assertion that actually protects the fix.

    ``referrals`` is absent from this deployment. It is tolerated ONLY because
    every statement that reads it is inside a ``to_regclass`` guard, so the
    migration skips the backfill instead of aborting. Remove the guard, or
    un-nest the statement, and this fails.
    """

    @pytest.mark.parametrize("relation", sorted(SQL_GUARDED_ABSENT_RELATIONS))
    def test_the_relation_is_still_referenced_and_still_undeclared(self, relation):
        """The premise. Without this, the test below could pass by vacuity -
        nothing references it, so nothing is unguarded."""
        filename, _form = SQL_GUARDED_ABSENT_RELATIONS[relation]
        references = _sql_dml_references()
        sites = references.get(relation, [])
        assert sites, (
            f"no SQL migration reads {relation!r} any more. If the legacy "
            "backfill has genuinely been retired, delete the entry from "
            "SQL_GUARDED_ABSENT_RELATIONS and the guarded statement with it."
        )
        assert any(f == filename for f, _l, _k, _o in sites), (
            f"{relation!r} is no longer referenced by {filename}: "
            f"{sorted({f for f, _l, _k, _o in sites})}"
        )
        declared = _sql_declared_relations()
        assert relation not in declared, (
            f"{relation!r} is now declared by {sorted(declared[relation])}. That "
            "is the better fix - remove it from SQL_GUARDED_ABSENT_RELATIONS so "
            "the general guard covers it and the to_regclass wrapper can go."
        )

    @pytest.mark.parametrize("relation", sorted(SQL_GUARDED_ABSENT_RELATIONS))
    def test_every_statement_touching_it_is_inside_an_existence_guard(self, relation):
        filename, form = SQL_GUARDED_ABSENT_RELATIONS[relation]
        matches = [p for p in _sql_migration_files() if p.name == filename]
        assert matches, f"{filename} is no longer in the migration set"
        guarded, unguarded = _guarded_sites(matches[0], relation, form)
        assert guarded, (
            f"no guarded reference to {relation!r} found in {filename} at all - "
            "the guard search is matching nothing, so this test proves nothing."
        )
        assert not unguarded, (
            f"{filename} reads {relation!r} OUTSIDE a {form} existence guard at "
            f"line(s) {[line for line, _kw in unguarded]}. {relation!r} does not "
            "exist in this deployment, so that statement aborts the whole file "
            f'with 42P01 relation "{relation}" does not exist and the migration '
            "stops being replayable. Wrap it like the others:\n"
            "    DO $guard$\n"
            "    BEGIN\n"
            f"        IF to_regclass('{relation}') IS NULL THEN\n"
            "            RAISE NOTICE '...skipping...';\n"
            "        ELSE\n"
            "            EXECUTE $sql$ <the original statement, verbatim> $sql$;\n"
            "        END IF;\n"
            "    END\n"
            "    $guard$;"
        )

    def test_the_referrals_backfill_is_guarded_not_deleted(self):
        """A guard that works by deleting the backfill is not the fix.

        On a database that still carries the legacy table this IS the correct
        migration, so the statements have to survive inside the guard.
        """
        assert REFERRAL_REDESIGN.is_file()
        masked = _mask_sql(REFERRAL_REDESIGN.read_text(encoding="utf-8"))
        for target in REFERRALS_BACKFILL_TARGETS:
            assert re.search(
                rf"INSERT\s+INTO\s+{target}\b", masked, re.IGNORECASE
            ), (
                f"the backfill into {target} is gone from "
                "referral_system_redesign.sql. The point of the guard is that "
                "the statement still runs where the legacy table exists."
            )
        guarded, unguarded = _guarded_sites(
            REFERRAL_REDESIGN, "referrals", "to_regclass"
        )
        assert len(guarded) == 2 and not unguarded, (
            "expected exactly two guarded reads of referrals (the "
            "referral_relationships backfill and the referral_wallets "
            f"initialisation); guarded={guarded} unguarded={unguarded}"
        )

    def test_prose_about_referrals_cannot_satisfy_the_guard(self):
        """The file's new header names ``referrals`` repeatedly, and so does a
        ``RAISE NOTICE``. Neither may count as a reference or as a guard."""
        raw = REFERRAL_REDESIGN.read_text(encoding="utf-8")
        mentions = len(re.findall(r"\breferrals\b", raw, re.IGNORECASE))
        assert mentions > 6, (
            "the header that explains the guard has been removed; this test "
            "exists because that prose must not be load-bearing."
        )
        sites = [
            (name, lineno)
            for name, lineno, _k, _o in _dml_relations_in(_mask_sql(raw))
            if name == "referrals"
        ]
        assert len(sites) == 2, (
            f"expected exactly 2 executable reads of referrals, found {sites} "
            f"against {mentions} mentions in the file. Comments and literals "
            "must contribute none."
        )


# --------------------------------------------------------------------------
# (f) a FILTER clause attaches to an aggregate, or the statement will not parse
#
# THE DEFECT THIS SECTION DETECTS
# -------------------------------
# `TestGuardedAbsentRelationsStayGuarded` above protects the GUARD. Nothing
# protected the guarded STATEMENT from being syntactically invalid, and one of
# them was. The `referral_wallets` initialisation read
#
#     COALESCE(SUM(r.commission_usd), 0) FILTER (WHERE r.status = 'pending')
#
# three times. `FILTER` attaches only to an aggregate call, never to an
# ordinary function, so PostgreSQL answered `42601 syntax error at or near
# "FILTER"` - verified on PostgreSQL 17.6, WITH the legacy `referrals` table
# present as well as without it, because 42601 is a parse failure and no table
# can satisfy it. That statement had therefore never executed successfully on
# any database.
#
# It was fixed by moving `FILTER` inside the `COALESCE` so it attaches to the
# `SUM`, keeping the `COALESCE` outside as the null-to-zero default for a
# profile with no referral rows. The guard above was unaffected.
#
# Two assertions, deliberately at different strengths:
#
# * a GENERAL rule - every executable `FILTER (WHERE ...)` anywhere in the
#   migration set must sit on a named aggregate. Tractable here because the
#   whole set contains exactly three, all in the corrected statement; the rest
#   of the matches on the word are prose and string literals, which the masking
#   removes.
# * a SPECIFIC rule - the wallets statement's four expressions keep their exact
#   shape and their status-to-column order. A statement that merely PARSES
#   would still be wrong if a `FILTER` landed on the wrong branch or on the
#   wrong status, and that is the whole class of bug here.
# --------------------------------------------------------------------------

#: An executable aggregate FILTER clause. `FILTER` with anything other than
#: `WHERE` after it is not this construct.
_SQL_FILTER_WHERE = re.compile(r"\bFILTER\s*\(\s*WHERE\b", re.IGNORECASE)

#: PostgreSQL aggregate functions - the ONLY things `FILTER` may follow.
#: Window functions are deliberately absent: `rank() FILTER (WHERE ...)` is
#: just as invalid as `COALESCE(...) FILTER (WHERE ...)`. If a genuine
#: aggregate is ever missing from this set, ADD IT - do not relax the rule.
_SQL_AGGREGATES = frozenset(
    """
    count sum avg min max every bool_and bool_or bit_and bit_or bit_xor
    array_agg string_agg json_agg jsonb_agg json_object_agg jsonb_object_agg
    json_objectagg jsonb_objectagg xmlagg range_agg range_intersect_agg
    multirange_agg stddev stddev_pop stddev_samp variance var_pop var_samp
    corr covar_pop covar_samp regr_avgx regr_avgy regr_count regr_intercept
    regr_r2 regr_slope regr_sxx regr_sxy regr_syy mode percentile_cont
    percentile_disc any_value
    """.split()
)

#: What :func:`_filter_clause_target` reports when `FILTER` does not follow a
#: closing parenthesis at all, so there is no call for it to attach to.
_FILTER_NOT_A_CALL = "<not a call>"


def _filter_clause_target(body: str, pos: int) -> str:
    """Name of the call whose closing ``)`` sits immediately before ``pos``.

    Walks backwards from the ``FILTER`` keyword: the token before it must be a
    ``)``; its matching ``(`` is found by depth; the identifier in front of
    that ``(`` is the call being filtered. ``agg(x) WITHIN GROUP (ORDER BY y)
    FILTER (...)`` is resolved through the ``WITHIN GROUP`` clause to ``agg``,
    which is how an ordered-set aggregate is written.
    """
    i = pos - 1
    while i >= 0 and body[i] in " \t\r\n":
        i -= 1
    if i < 0 or body[i] != ")":
        return _FILTER_NOT_A_CALL
    depth = 0
    while i >= 0:
        if body[i] == ")":
            depth += 1
        elif body[i] == "(":
            depth -= 1
            if depth == 0:
                break
        i -= 1
    if i < 0:
        return _FILTER_NOT_A_CALL
    j = i - 1
    while j >= 0 and body[j] in " \t\r\n":
        j -= 1
    end = j + 1
    while j >= 0 and _IDENT_CHAR.match(body[j]):
        j -= 1
    name = body[j + 1:end].lower()
    if name == "group":
        k = j
        while k >= 0 and body[k] in " \t\r\n":
            k -= 1
        within_end = k + 1
        while k >= 0 and _IDENT_CHAR.match(body[k]):
            k -= 1
        if body[k + 1:within_end].lower() == "within":
            return _filter_clause_target(body, k + 1)
    return name or _FILTER_NOT_A_CALL


def _filter_clause_sites(masked: str):
    """Yield ``(lineno, target)`` for every executable ``FILTER (WHERE ...)``.

    ``masked`` must come from :func:`_mask_sql`, so the word in a comment or
    inside a ``RAISE NOTICE`` literal - and this file's own header quotes the
    broken form twice - contributes nothing.
    """
    for match in _SQL_FILTER_WHERE.finditer(masked):
        yield (
            masked.count("\n", 0, match.start()) + 1,
            _filter_clause_target(masked, match.start()),
        )


#: The exact shape each per-status expression must keep: the aggregate filtered
#: INSIDE, the COALESCE default OUTSIDE. Both halves matter - see the class.
_WALLET_STATUS_EXPR = re.compile(
    r"COALESCE\s*\(\s*SUM\s*\(\s*r\.commission_usd\s*\)\s*"
    r"FILTER\s*\(\s*WHERE\s+r\.status\s*=\s*'(\w+)'\s*\)\s*,\s*0\s*\)",
    re.IGNORECASE,
)
#: The unfiltered total. Carries no FILTER and must not acquire one.
_WALLET_LIFETIME_EXPR = re.compile(
    r"COALESCE\s*\(\s*SUM\s*\(\s*r\.commission_usd\s*\)\s*,\s*0\s*\)(?!\s*FILTER)",
    re.IGNORECASE,
)
#: The file has three ``INSERT INTO referral_wallets``: ``(user_id)`` in
#: ``create_referral_code_for_user``, ``(user_id, pending_balance_usd,
#: lifetime_earnings_usd)`` in ``process_referral_commission``, and the
#: backfill. Only the backfill names all four balance columns, so that - and
#: not merely the first match - is what this selects.
_WALLET_INSERT_COLUMNS = re.compile(
    r"INSERT\s+INTO\s+referral_wallets\s*\(([^)]*)\)", re.IGNORECASE
)
_WALLET_BALANCE_COLUMNS = frozenset(
    {"pending_balance_usd", "approved_balance_usd", "paid_balance_usd",
     "lifetime_earnings_usd"}
)

_FILTER_FIXTURE_BROKEN = (
    "SELECT COALESCE(SUM(r.commission_usd), 0) FILTER (WHERE r.status = 'pending')\n"
    "FROM profiles p LEFT JOIN referrals r ON r.referrer_id = p.id GROUP BY p.id;\n"
)
_FILTER_FIXTURE_FIXED = (
    "SELECT COALESCE(SUM(r.commission_usd) FILTER (WHERE r.status = 'pending'), 0)\n"
    "FROM profiles p LEFT JOIN referrals r ON r.referrer_id = p.id GROUP BY p.id;\n"
)
_FILTER_FIXTURE_PROSE = """
-- COALESCE(SUM(x), 0) FILTER (WHERE y = 1) is the form that will not parse.
DO $$
BEGIN
    RAISE NOTICE 'COALESCE(SUM(x), 0) FILTER (WHERE y = 1) raises 42601';
END $$;
"""
_FILTER_FIXTURE_OTHER_SHAPES = """
SELECT count(*) FILTER (WHERE status IS NULL),
       percentile_cont(0.5) WITHIN GROUP (ORDER BY amount) FILTER (WHERE amount > 0),
       jsonb_agg(id) FILTER (WHERE status = 'paid'),
       GREATEST(sum(amount), 0) FILTER (WHERE status = 'paid')
FROM real_one;
"""


class TestFilterClausesAttachToAggregates:
    """``FILTER`` on a non-aggregate is ``42601``: the statement never parses,
    so it has never run anywhere and there is no behaviour to preserve."""

    def test_the_checker_fires_on_the_broken_form(self):
        """The premise, and the thing that was actually wrong. Without this the
        general assertion below could pass by never matching anything."""
        sites = list(_filter_clause_sites(_mask_sql(_FILTER_FIXTURE_BROKEN)))
        assert [target for _line, target in sites] == ["coalesce"], sites
        assert "coalesce" not in _SQL_AGGREGATES, (
            "COALESCE is not an aggregate; if it is ever added to "
            "_SQL_AGGREGATES this whole class stops detecting anything."
        )

    def test_the_checker_accepts_the_corrected_form(self):
        sites = list(_filter_clause_sites(_mask_sql(_FILTER_FIXTURE_FIXED)))
        assert [target for _line, target in sites] == ["sum"], sites
        assert "sum" in _SQL_AGGREGATES

    def test_prose_and_literals_contribute_no_filter_sites(self):
        """The migration's own header quotes the broken form, and so could a
        ``RAISE NOTICE``. Neither may trip or satisfy this class."""
        assert list(_filter_clause_sites(_mask_sql(_FILTER_FIXTURE_PROSE))) == []

    def test_the_checker_resolves_the_other_shapes_it_will_meet(self):
        """Bare aggregate, ordered-set aggregate through ``WITHIN GROUP``, a
        non-``SUM`` aggregate, and a second non-aggregate wrapper."""
        found = [t for _line, t in _filter_clause_sites(_mask_sql(_FILTER_FIXTURE_OTHER_SHAPES))]
        assert found == ["count", "percentile_cont", "jsonb_agg", "greatest"], found
        offenders = [t for t in found if t not in _SQL_AGGREGATES]
        assert offenders == ["greatest"], (
            f"expected GREATEST to be the only offender, got {offenders}"
        )

    def test_every_filter_in_the_migration_set_is_on_an_aggregate(self):
        """The general rule, over every hand-applied SQL migration."""
        offenders = []
        total = 0
        for path in _sql_migration_files():
            masked = _mask_sql(path.read_text(encoding="utf-8", errors="replace"))
            for lineno, target in _filter_clause_sites(masked):
                total += 1
                if target not in _SQL_AGGREGATES:
                    offenders.append((path.name, lineno, target))
        assert total >= 3, (
            f"only {total} executable FILTER clauses found in "
            f"{len(_sql_migration_files())} SQL migrations. The "
            "referral_wallets initialisation alone has three, so the scan has "
            "stopped matching and this assertion is vacuous."
        )
        if offenders:
            pytest.fail(
                "FILTER attaches only to an AGGREGATE call in PostgreSQL. These "
                "clauses attach to something else, so the statement raises "
                "42601 syntax error at or near \"FILTER\" and can never run - "
                "on any database, with or without the relations it reads:\n"
                + "\n".join(
                    f"  {name}:{lineno}  FILTER follows {target!r}"
                    for name, lineno, target in offenders
                )
                + "\n\nMove the FILTER inside, onto the aggregate: "
                "COALESCE(SUM(x) FILTER (WHERE ...), 0), not "
                "COALESCE(SUM(x), 0) FILTER (WHERE ...). If the call really is "
                "an aggregate, add its name to _SQL_AGGREGATES."
            )

    def test_the_wallets_initialisation_keeps_its_exact_shape(self):
        """SPECIFIC, not general: parsing is not enough.

        A ``FILTER`` on the wrong branch of the ``COALESCE``, or on the wrong
        status, parses perfectly and writes the wrong numbers. Proven against
        PostgreSQL 17.6: a referrer with a 10.00 ``pending`` row and a 5.00
        ``approved`` row lands ``pending_balance_usd=10.00``,
        ``approved_balance_usd=5.00``, ``paid_balance_usd=0.00`` and
        ``lifetime_earnings_usd=15.00``; a profile with no referral row at all
        lands four zeros rather than four NULLs, which is what the outer
        ``COALESCE`` is for.
        """
        masked = _mask_sql(
            REFERRAL_REDESIGN.read_text(encoding="utf-8"), mask_literals=False
        )
        column_lists = [
            [c.strip().lower() for c in m.group(1).split(",")]
            for m in _WALLET_INSERT_COLUMNS.finditer(masked)
        ]
        backfill = [
            cols for cols in column_lists
            if _WALLET_BALANCE_COLUMNS.issubset(cols)
        ]
        assert len(backfill) == 1, (
            "expected exactly one INSERT INTO referral_wallets naming all four "
            f"balance columns (the backfill); found {column_lists}"
        )
        columns = backfill[0]
        expected_statuses = [
            c[: -len("_balance_usd")] for c in columns if c.endswith("_balance_usd")
        ]
        assert expected_statuses == ["pending", "approved", "paid"], columns

        statuses = [m.group(1).lower() for m in _WALLET_STATUS_EXPR.finditer(masked)]
        assert statuses == expected_statuses, (
            "the per-status expressions in the referral_wallets initialisation "
            f"are {statuses}, and the INSERT writes them into {columns}. Each "
            "must be COALESCE(SUM(r.commission_usd) FILTER (WHERE r.status = "
            "'<status>'), 0) in the SAME ORDER as the target columns:\n"
            "  * FILTER outside the COALESCE is 42601 - it has never parsed;\n"
            "  * COALESCE inside the FILTER, or dropped, writes NULL for a "
            "profile with no referral rows and trips "
            "chk_non_negative_balances;\n"
            "  * the statuses out of order silently writes one balance into "
            "another's column."
        )
        assert len(_WALLET_LIFETIME_EXPR.findall(masked)) == 1, (
            "lifetime_earnings_usd must stay the UNFILTERED "
            "COALESCE(SUM(r.commission_usd), 0) - it is the total across every "
            "status, including ones no balance column buckets."
        )

# --------------------------------------------------------------------------
# (g) one level deeper than a table: the COLUMNS the application names
#
# THE DEFECT CLASS THIS SECTION DETECTS
# -------------------------------------
# Everything above asks whether the RELATION exists. A relation that exists
# and lacks the column a read projects answers `42703 column ... does not
# exist`, which is a different failure with the same cause: the declared
# schema and the applied schema disagree, and nothing asked.
#
# This has already shipped three times in this repository:
#
# * the exchange-list read, fixed earlier in this spec;
# * creator analytics projecting `monthly_price` and `rating_average` off
#   `library_strategies`, where neither column exists
#   (`tests/test_creator_analytics_regression.py`);
# * `strategy_backtests.version` declared `VARCHAR(20)` by
#   `001_strategy_architecture.sql` and `INTEGER` by
#   `006_reconcile_production_database.sql`
#   (`tests/test_backtest_version_label_regression.py`, and 016's header).
#
# WHAT IS PARSED, AND WHY EACH FORM IS HERE
# -----------------------------------------
# The reference side is every column a `backend_app/**/*.py` call site names
# against a table the chain resolves:
#
# * `.select("a, b, c")` - PostgREST syntax. `*`; `count`; aliases
#   `alias:col`; casts `col::text`; JSON paths `col->k` / `col->>k`;
#   aggregates `amount.sum()`; and embedded resources `rel(a,b)` /
#   `rel!hint(a,b)` / `rel!fk!inner(a,b)`, which are NOT columns of the outer
#   table - the hint is stripped and the embed is FOLLOWED into `rel`, where
#   its own list is parsed recursively. `library_entries` builds three such
#   projections by concatenating `listing_projection.LISTING_SELECT` inside a
#   `library_strategies!inner(...)` embed, so getting this wrong would have
#   reported 36 columns against the wrong relation.
# * `.eq/.neq/.gt/.gte/.lt/.lte/.like/.ilike/.is_/.in_/.contains/.order/
#   .filter(...)` - the first positional argument is a column. A dotted
#   `.eq("rel.col", v)` addresses an embedded resource and is attributed to
#   `rel`, not to the outer table.
# * `.insert({...})` / `.update({...})` / `.upsert({...})` with a LITERAL
#   dict - the keys are columns. A payload built dynamically is SKIPPED and
#   COUNTED, never guessed.
# * `on_conflict="col"`, including the comma-separated form.
#
# The chain walk is over the AST receiver spine, not a line-oriented regex: a
# `.select()` five lines below its `.table()` inside a parenthesised chain is
# the normal shape here. Local variables assigned from a resolved chain are
# tracked PER SCOPE and IN STATEMENT ORDER - a single module-wide pass was
# tried and was wrong: two handlers in `routers/library.py` both build a local
# called `query`, one on `library_strategies` and one on
# `library_subscriptions`, and last-write-wins attributed the first handler's
# eight filters to the second handler's table.
#
# Module-level string constants ARE resolved, including `+` concatenation and
# cross-module `mod.NAME`. Nothing else is: a loop variable over a list of
# table names (`for table in EXCHANGE_ACCOUNT_TABLES`) is genuinely ambiguous,
# so it is skipped and counted. :data:`MAX_UNRESOLVABLE_COLUMN_CALL_SITES` is
# the ratchet that stops that count growing quietly.
#
# THE ORACLE
# ----------
# `CREATE TABLE` column lists PLUS every `ALTER TABLE ... ADD COLUMN` /
# `DROP COLUMN` / `RENAME COLUMN` / `ALTER COLUMN ... TYPE`, applied in file
# order. The `ALTER` half is not optional:
# `006_reconcile_production_database.sql` adds eleven columns to `profiles`
# and nineteen to `strategies` after the fact, so a `CREATE TABLE`-only parse
# reports a flood of columns as missing that are not.
#
# Four tables this application reads have NO `CREATE TABLE` anywhere in the
# migration set - they predate it. For those, and only those, the production
# column set is RECORDED in
# :data:`PRODUCTION_COLUMNS_FOR_UNCREATED_TABLES` with the server and the
# count it was read from. That is the same evidence pattern
# `SQL_UNDECLARED_PRESENT_IN_PRODUCTION` already uses for row counts, and it
# is recorded rather than parsed for the same reason: there is no file in this
# checkout to parse it out of, and this suite takes no network.
#
# NO NETWORK HERE EITHER
# ----------------------
# Every assertion below is a parse of files in the checkout. The production
# column sets and the `42703` answers quoted in the messages were established
# by a read-only probe against PostgreSQL 17.6 and are recorded; they are not
# re-read at test time.
# --------------------------------------------------------------------------

#: The tables this application reads that NO ``CREATE TABLE`` in the migration
#: set declares, with their column sets AS READ FROM PRODUCTION (PostgreSQL
#: 17.6, 71 base tables in ``public``, ``profiles`` 180 rows). The migration
#: set only ``ALTER``s these, so its column list for them is a subset by
#: construction and cannot be the oracle on its own.
#:
#: Recorded, not parsed, because nothing in this checkout declares them -
#: which is the drift itself, and is already recorded at the table level by
#: ``SQL_UNDECLARED_PRESENT_IN_PRODUCTION``. Adding a column here to silence a
#: failure RE-CREATES the defect: the name belongs here only if production
#: already has it, and a column production does not have needs a migration,
#: not an entry.
PRODUCTION_COLUMNS_FOR_UNCREATED_TABLES = {
    "profiles": frozenset(  # 20 columns
        {
            "available_discounts", "avatar_url", "balance", "billing_currency",
            "bio", "created_at", "deployed_bots", "display_name", "email",
            "full_name", "id", "is_frozen", "max_api_slots",
            "ml_addons_purchased", "ml_strategies_built", "preferred_currency",
            "subscription_tier", "telegram_id", "updated_at", "username",
        }
    ),
    "strategies": frozenset(  # 26 columns
        {
            "archived_at", "backtest_result", "buy_logic", "created_at",
            "current_version", "dag_config", "dag_hash", "dag_schema_version",
            "dag_version", "environment", "exchange_id", "execution_order",
            "id", "indicators", "is_active", "last_signal_at", "ml_model_path",
            "name", "risk", "sell_logic", "source_library_id", "status",
            "symbol", "timeframe", "updated_at", "user_id",
        }
    ),
    "execution_records": frozenset(  # 22 columns
        {
            "avg_price", "created_at", "exchange_id", "exchange_status",
            "execution_id", "filled_at", "filled_size", "last_exchange_sync",
            "order_id", "price", "remaining_size", "result", "side", "size",
            "status", "strategy_id", "submitted_at", "symbol", "task_id",
            "tenant_id", "updated_at", "user_id",
        }
    ),
    "library_strategies": frozenset(  # 52 columns
        {
            "author_id", "avg_rating", "backtest_end_date",
            "backtest_initial_capital", "backtest_max_drawdown_pct",
            "backtest_profit_factor", "backtest_sharpe_ratio",
            "backtest_start_date", "backtest_total_return_pct",
            "backtest_total_trades", "backtest_win_rate_pct", "category",
            "clone_count", "condition_count", "cover_image", "currency",
            "deployment_requirements", "description", "difficulty",
            "equity_curve_snapshot", "evaluation_score", "exchange_id",
            "has_ml_model", "id", "is_active", "is_featured", "market_type",
            "moderated_at", "moderated_by", "moderation_notes",
            "moderation_status", "name", "node_count", "price", "price_minor",
            "published_at", "rating_count", "risk_max_drawdown_pct",
            "risk_max_position_size", "risk_stop_loss_pct",
            "risk_take_profit_pct", "source_cloning_enabled",
            "source_strategy_id", "subscriber_count", "subscription_tier",
            "supported_timeframes", "symbol", "tags", "timeframe",
            "updated_at", "verification_status", "version_history",
        }
    ),
    "library_ratings": frozenset(  # 8 columns; declared only by Alembic
        {
            "created_at", "id", "is_verified_clone", "library_id", "rating",
            "review_text", "updated_at", "user_id",
        }
    ),
}

#: A REGISTER OF OPEN DEFECTS, not an allowlist. Every entry was proven to
#: answer ``42703`` against production (PostgreSQL 17.6, read-only
#: ``SELECT "<col>" FROM public."<table>" LIMIT 0``) alongside a control
#: column on the same table that answered ``OK``. They are listed so the guard
#: can fail on a NEW one; the count may SHRINK and may never grow.
#:
#: The value is (owning module, why it is not fixed here). Each reason is a
#: statement about WHO decides, not an excuse:
#:
#: * the ``billing_invoices`` and ``profiles`` billing columns sit in the
#:   parallel pricing/entitlements workstream's files. ``routers/billing.py``
#:   carries their uncommitted changes; editing it here would collide.
#: * ``routers/signals.py`` is mounted at ``/api/signals`` and has NO frontend
#:   caller - the Signal_Trace page calls ``/api/signal-trace/signals`` on
#:   ``routers/signal_trace.py`` instead. ``execution_records`` is an
#:   order-execution table with no ``indicators``, ``ml_inputs``,
#:   ``ml_outputs``, ``confidence``, ``risk_verdict``, ``timeframe``,
#:   ``latency_ms``, ``pnl`` or ``failure_reason`` to repoint those twelve
#:   names AT. Choosing between deleting a mounted router and inventing nine
#:   columns is not a parse's decision.
#:
#: ``strategies.tenant_id`` WAS the 24th entry and is now FIXED at its root
#: (production-launch-hardening task 13.18), so it has left the register: the
#: marketplace eligibility gate reads the tenant off ``user_id``, which is what
#: this platform's tenancy model means and the column production has. See
#: :data:`FIXED_ELIGIBILITY_TENANT_PREDICATE` and the test that pins it.
KNOWN_UNDECLARED_COLUMN_DEFECTS = {
    "billing_invoices.amount_inr": ("routers/billing.py", "parallel workstream"),
    "billing_invoices.amount_usd": ("routers/billing.py", "parallel workstream"),
    "billing_invoices.plan": ("routers/billing.py", "parallel workstream"),
    "billing_invoices.provider": ("routers/billing.py", "parallel workstream"),
    "billing_invoices.provider_payment_id": (
        "routers/billing.py", "parallel workstream",
    ),
    "profiles.cancel_at_period_end": ("routers/billing.py", "parallel workstream"),
    "profiles.next_billing_date": ("core/billing_lifecycle.py", "parallel workstream"),
    "profiles.pending_downgrade_tier": (
        "core/billing_lifecycle.py", "parallel workstream",
    ),
    "profiles.subscription_status": (
        "core/billing_lifecycle.py", "parallel workstream",
    ),
    "profiles.trial_end_date": ("core/billing_lifecycle.py", "parallel workstream"),
    "profiles.volume_usd": ("routers/admin.py", "no source of truth anywhere"),
    "execution_records.confidence": ("routers/signals.py", "unreachable router"),
    "execution_records.failure_reason": ("routers/signals.py", "unreachable router"),
    "execution_records.filled_quantity": ("routers/signals.py", "unreachable router"),
    "execution_records.id": ("routers/signals.py", "unreachable router"),
    "execution_records.indicators": ("routers/signals.py", "unreachable router"),
    "execution_records.latency_ms": ("routers/signals.py", "unreachable router"),
    "execution_records.ml_inputs": ("routers/signals.py", "unreachable router"),
    "execution_records.ml_outputs": ("routers/signals.py", "unreachable router"),
    "execution_records.pnl": ("routers/signals.py", "unreachable router"),
    "execution_records.quantity": ("routers/signals.py", "unreachable router"),
    "execution_records.risk_verdict": ("routers/signals.py", "unreachable router"),
    "execution_records.timeframe": ("routers/signals.py", "unreachable router"),
}

#: The column reference `routers/library.py` used to carry and no longer does:
#: `_enrich_cards_with_user_context` read `library_strategies` filtered on
#: `source_library_id`, which that table does not have - the clone lives in
#: `strategies`, which is where `clone_strategy` inserts it and where its own
#: idempotency check reads it back. Pinned as a pair so the wrong table cannot
#: come back.
FIXED_CLONE_ENRICHMENT = ("strategies", "library_strategies", "source_library_id")

#: The column reference the marketplace Eligibility_Gate used to carry and no
#: longer does, pinned as (table, the absent column, the column that exists).
#: Read 1 of ``backend/marketplace/eligibility_gate.py`` selected
#: ``id, user_id, tenant_id, archived_at`` off ``strategies``, which has no
#: ``tenant_id``; the gate wraps its four reads so that ANY failure answers
#: ``unevaluable=True``, so every marketplace submission eligibility evaluation
#: in production answered "unknown" and the route turned it into a 503 - nothing
#: could be published at all. Fixed in production-launch-hardening task 13.18 by
#: reading the tenant off ``user_id``: this platform's tenancy model is
#: tenant == user (``tenant-boundary-audit.md`` records ``strategies``' tenant
#: predicate as ``user_id``; ``core/dependencies.py`` defaults a caller's
#: ``tenant_id`` to their own ``user_id``), so no column was added to a live
#: table to encode a distinction the system does not make.
FIXED_ELIGIBILITY_TENANT_PREDICATE = ("strategies", "tenant_id", "user_id")

#: Call sites whose owning table could not be resolved with confidence, as
#: observed when this guard was written. A RATCHET: coverage can erode to zero
#: while every assertion still passes, so the count may shrink and may never
#: grow. 52 of these sit in the parallel workstream's files, so a small rise
#: there is expected to be explained rather than absorbed.
MAX_UNRESOLVABLE_COLUMN_CALL_SITES = 182

#: Call sites that DID resolve, as observed. The other half of the ratchet: a
#: parser that stops matching would otherwise satisfy every assertion above.
MIN_RESOLVED_COLUMN_CALL_SITES = 980

#: Floors on the parse itself, in the style of
#: ``test_declared_set_is_parsed_and_non_trivial``.
MIN_DISTINCT_COLUMN_PAIRS = 450
MIN_TABLES_WITH_COLUMN_REFERENCES = 40
MIN_DECLARED_COLUMN_PAIRS = 800

# --------------------------------------------------------------------------
# the PostgREST select-list parser
# --------------------------------------------------------------------------

#: Projection items that are not column names.
_SELECT_PSEUDO_COLUMNS = frozenset({"*", "count"})

#: Aggregate suffixes PostgREST accepts as ``col.sum()``.
_SELECT_AGGREGATE_SUFFIXES = frozenset({"count", "sum", "avg", "min", "max"})

_PLAIN_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_JSON_PATH_OPERATOR = re.compile(r"->>?")


def _split_at_depth_zero(text: str, separator: str = ","):
    """Split ``text`` on ``separator`` at parenthesis depth zero.

    Shared by the select-list parser and the ``CREATE TABLE`` column-list
    parser, which have the same problem: a comma inside ``numeric(10,2)`` or
    inside an embed's own list is not a separator.
    """
    parts, depth, current = [], 0, []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == separator and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return parts


def _strip_select_alias(item: str) -> str:
    """``alias:col`` -> ``col``. A ``::`` cast is not an alias."""
    depth = 0
    i = 0
    while i < len(item):
        ch = item[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == ":" and depth == 0:
            if item[i + 1:i + 2] == ":":
                i += 2
                continue
            return item[i + 1:].strip()
        i += 1
    return item


def _select_base_column(token: str):
    """One projection token reduced to the column it names, or ``None``.

    Strips a ``::`` cast, a ``->``/``->>`` JSON path, and a ``col.sum()``
    aggregate wrapper; answers ``None`` for ``*``, for bare ``count()`` and
    for anything that is not a plain identifier afterwards.
    """
    token = token.strip()
    if not token:
        return None
    token = token.split("::", 1)[0].strip()
    token = _JSON_PATH_OPERATOR.split(token, maxsplit=1)[0].strip()
    if token.endswith("()"):
        token = token[:-2].strip()
        if "." in token:
            head, tail = token.rsplit(".", 1)
            if tail.lower() in _SELECT_AGGREGATE_SUFFIXES:
                token = head.strip()
        else:
            return None
    if not token or token in _SELECT_PSEUDO_COLUMNS:
        return None
    if not _PLAIN_IDENTIFIER.match(token):
        return None
    return token.lower()


def _parse_select_list(expr: str, table: str, sink, skips, site) -> None:
    """Attribute every column in a PostgREST select list to its own table.

    ``sink(table, column)`` per column. An embedded resource recurses into the
    embedded relation; ``!hint`` and ``!fk!inner`` are stripped from the head
    first, because the hint is not part of the relation name.
    """
    for raw in _split_at_depth_zero(expr):
        item = raw.strip()
        if not item:
            continue
        if item.startswith("..."):  # PostgREST spread embed
            item = item[3:].strip()
        item = _strip_select_alias(item)
        open_paren = item.find("(")
        if open_paren != -1 and item.endswith(")"):
            body = item[open_paren + 1:-1]
            head = item[:open_paren].strip()
            if not body.strip():
                column = _select_base_column(item)
                if column:
                    sink(table, column)
                continue
            relation = _strip_select_alias(head.split("!", 1)[0].strip()).lower()
            if not _PLAIN_IDENTIFIER.match(relation or ""):
                skips.append((site, "unparsable embed %r" % item))
                continue
            _parse_select_list(body, relation, sink, skips, site)
            continue
        column = _select_base_column(item)
        if column:
            sink(table, column)


# --------------------------------------------------------------------------
# the Python call-site scanner
# --------------------------------------------------------------------------

_COLUMN_TABLE_VERBS = ("table", "from_")

#: First positional argument is a column name.
_COLUMN_FILTER_METHODS = frozenset(
    {
        "eq", "neq", "gt", "gte", "lt", "lte", "like", "ilike", "is_", "in_",
        "contains", "contained_by", "order", "filter", "like_all_of",
        "like_any_of", "ilike_all_of", "ilike_any_of", "overlaps",
        "text_search", "range_gt", "range_gte", "range_lt", "range_lte",
        "range_adjacent", "not_",
    }
)
_COLUMN_SELECT_METHODS = frozenset({"select"})
_COLUMN_WRITE_METHODS = frozenset({"insert", "update", "upsert"})

#: Statement fields that are nested SUITES. Excluded from the per-statement
#: expression walk and recursed separately, so the statements inside them are
#: seen in order relative to the assignments around them.
_SUITE_FIELDS = ("body", "orelse", "finalbody", "handlers")

#: A rebinding that would make one variable mean two tables. The binding is
#: dropped rather than guessed.
_COLUMN_CONFLICTED = object()


def _constant_str(node, constants=None):
    """The string this expression names, following module constants."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if constants is None:
        return None
    return constants.resolve(node)


class _ConstantIndex:
    """Module-level string constants across ``backend_app/``, with imports.

    Indexing, parsing and resolution are all LAZY and cached: a module is
    parsed when a scan or a lookup first reaches it, and the tree is shared.

    Constants are resolved because the densest projections in this codebase
    are constants - ``listing_projection.LISTING_SELECT`` names 25 columns and
    is read by nine handlers, and ``library_entries`` concatenates it inside
    an embed three more times. Treating them as unresolvable would leave the
    guard blind to the reads that name the most columns. Nothing beyond a
    module-level string and ``+`` concatenation of them is resolved: an
    f-string, a loop variable or a dict lookup is genuinely ambiguous.
    """

    def __init__(self, root: Path, repo_root: Path) -> None:
        self.root = root
        self.repo_root = repo_root
        self.assignments: dict = {}
        self.imports: dict = {}
        self._memo: dict = {}
        self._active = set()
        self._trees: dict = {}
        self._paths = {
            self.module_name(path): path for path in sorted(root.rglob("*.py"))
        }

    def module_name(self, path: Path) -> str:
        parts = list(path.relative_to(self.repo_root).with_suffix("").parts)
        if parts and parts[-1] == "__init__":
            parts.pop()
        return ".".join(parts)

    def tree(self, path: Path):
        key = str(path)
        if key not in self._trees:
            try:
                self._trees[key] = ast.parse(
                    path.read_text(encoding="utf-8", errors="replace")
                )
            except SyntaxError:
                self._trees[key] = None
        return self._trees[key]

    def _index(self, module: str) -> bool:
        if module in self.assignments:
            return True
        path = self._paths.get(module)
        if path is None:
            return False
        tree = self.tree(path)
        names: dict = {}
        imports: dict = {}
        if tree is not None:
            for stmt in tree.body:
                if isinstance(stmt, ast.Assign):
                    for target in stmt.targets:
                        if isinstance(target, ast.Name):
                            names[target.id] = stmt.value
                elif isinstance(stmt, ast.AnnAssign) and isinstance(
                    stmt.target, ast.Name
                ):
                    if stmt.value is not None:
                        names[stmt.target.id] = stmt.value
                elif isinstance(stmt, ast.ImportFrom):
                    base = self._absolute(module, stmt)
                    for alias in stmt.names:
                        imports[alias.asname or alias.name] = (base, alias.name)
                elif isinstance(stmt, ast.Import):
                    for alias in stmt.names:
                        local = alias.asname or alias.name.split(".")[0]
                        imports[local] = (alias.name, None)
        self.assignments[module] = names
        self.imports[module] = imports
        return True

    @staticmethod
    def _absolute(module: str, stmt: ast.ImportFrom) -> str:
        if not stmt.level:
            return stmt.module or ""
        parts = module.split(".")
        base = parts[:-1] if stmt.level == 1 else parts[: len(parts) - stmt.level + 1]
        prefix = ".".join(base)
        return "%s.%s" % (prefix, stmt.module) if stmt.module else prefix

    def bind(self, module: str):
        return _BoundConstants(self, module)

    def lookup(self, module: str, name: str):
        key = (module, name)
        if key in self._memo:
            return self._memo[key]
        if key in self._active:
            return None  # a cycle between two modules' constants
        self._active.add(key)
        try:
            value = self._lookup(module, name)
        finally:
            self._active.discard(key)
        self._memo[key] = value
        return value

    def _lookup(self, module: str, name: str):
        self._index(module)
        node = self.assignments.get(module, {}).get(name)
        if node is not None:
            return self.evaluate(module, node)
        target = self.imports.get(module, {}).get(name)
        if target is None:
            return None
        other, original = target
        if original is None:
            return None  # the local name is a MODULE, not a string
        if self._index(other):
            return self.lookup(other, original)
        return None

    def evaluate(self, module: str, node):
        if isinstance(node, ast.Constant):
            return node.value if isinstance(node.value, str) else None
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = self.evaluate(module, node.left)
            right = self.evaluate(module, node.right)
            if left is None or right is None:
                return None
            return left + right
        if isinstance(node, ast.Name):
            return self.lookup(module, node.id)
        if isinstance(node, ast.Attribute):
            return self._attribute(module, node)
        return None

    def _attribute(self, module: str, node: ast.Attribute):
        parts = []
        cursor = node
        while isinstance(cursor, ast.Attribute):
            parts.append(cursor.attr)
            cursor = cursor.value
        if not isinstance(cursor, ast.Name):
            return None
        parts.append(cursor.id)
        parts.reverse()
        attribute, head = parts[-1], parts[:-1]
        if not head:
            return None
        self._index(module)
        target = self.imports.get(module, {}).get(head[0])
        if target is None:
            return None
        base, original = target
        if original is None:
            candidate = ".".join([base] + head[1:])
        elif len(head) == 1:
            candidate = "%s.%s" % (base, original) if base else original
        else:
            candidate = ".".join([base, original] + head[1:])
        if self._index(candidate):
            return self.lookup(candidate, attribute)
        return None


class _BoundConstants:
    """A constant resolver bound to one module's namespace."""

    def __init__(self, index: _ConstantIndex, module: str) -> None:
        self.index = index
        self.module = module

    def resolve(self, node):
        try:
            return self.index.evaluate(self.module, node)
        except RecursionError:  # pragma: no cover - a pathological constant
            return None


class _ColumnChainResolver:
    """Resolves the ``.table("x")`` a column-bearing method call belongs to."""

    def __init__(self, var_tables=None, constants=None) -> None:
        self.var_tables = dict(var_tables or {})
        self.constants = constants

    def resolve(self, call):
        node = call
        for _step in range(64):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute):
                    if func.attr in _COLUMN_TABLE_VERBS and node.args:
                        literal = _constant_str(node.args[0], self.constants)
                        if literal is not None:
                            return literal.lower(), None
                        return None, "table name is not a literal"
                    node = func.value
                    continue
                if isinstance(func, ast.Name):
                    return None, "chain starts at the bare call %r" % func.id
                return None, "chain starts at an unsupported callee"
            if isinstance(node, ast.Attribute):
                node = node.value
                continue
            if isinstance(node, (ast.Subscript, ast.Await)):
                node = node.value
                continue
            if isinstance(node, ast.Name):
                bound = self.var_tables.get(node.id)
                if bound is _COLUMN_CONFLICTED:
                    return None, "%r is bound to two tables here" % node.id
                if bound is not None:
                    return bound, None
                return None, "chain starts at the variable %r" % node.id
            return None, "chain starts at %s" % type(node).__name__
        return None, "chain too deep"  # pragma: no cover

    def note_assignment(self, stmt, branching: bool = False) -> None:
        """Record ``name = <chain>`` when the chain resolves to a table."""
        if isinstance(stmt, ast.Assign):
            targets, value = stmt.targets, stmt.value
        elif isinstance(stmt, (ast.AnnAssign, ast.AugAssign)):
            targets, value = [stmt.target], stmt.value
        else:
            return
        if value is None:
            return
        table = None
        chainlike = isinstance(value, (ast.Call, ast.Attribute, ast.Await))
        if chainlike:
            node = value.value if isinstance(value, ast.Await) else value
            table, _why = self.resolve(node)
        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            if table is None:
                self.var_tables.pop(target.id, None)
                continue
            previous = self.var_tables.get(target.id)
            if branching and previous not in (None, table):
                self.var_tables[target.id] = _COLUMN_CONFLICTED
            else:
                self.var_tables[target.id] = table


def _calls_in_statement(stmt):
    """Every ``Call`` in this statement's own expressions.

    A manual stack rather than :func:`ast.walk`: with
    :func:`ast.iter_fields` it was half the runtime of the whole scan.
    """
    collected = []
    stack = []
    for field in stmt._fields:
        if field in _SUITE_FIELDS:
            continue
        value = getattr(stmt, field, None)
        if isinstance(value, list):
            stack.extend(item for item in value if isinstance(item, ast.AST))
        elif isinstance(value, ast.AST):
            stack.append(value)
    while stack:
        node = stack.pop()
        if isinstance(node, ast.Call):
            collected.append(node)
        for field in node._fields:
            value = getattr(node, field, None)
            if isinstance(value, list):
                stack.extend(item for item in value if isinstance(item, ast.AST))
            elif isinstance(value, ast.AST):
                stack.append(value)
    return collected


def _keyword(node, name):
    for kw in node.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _scan_module_columns(path, tree, relative, bound, references, skips, counters):
    """Collect ``(table, column) -> [(file, line)]`` out of one module."""

    def attribute_filter(name, table, sink, site):
        """``.eq("col", v)`` or ``.eq("rel.col", v)``.

        A dotted filter addresses an EMBEDDED resource's column; attributing
        it to the outer table would invent a column there.
        """
        name = name.strip()
        if not name:
            return
        if "." in name:
            head, tail = name.split(".", 1)
            if _PLAIN_IDENTIFIER.match(head) and "." not in tail:
                column = _select_base_column(tail)
                if column:
                    sink(head.lower(), column)
                    return
            skips.append((site, "dotted filter path %r" % name))
            return
        column = _select_base_column(name)
        if column:
            sink(table, column)

    def visit_call(node, resolver):
        if not isinstance(node.func, ast.Attribute):
            return
        method = node.func.attr
        lineno = node.func.value.end_lineno or node.lineno
        site = "%s:%d .%s" % (relative, lineno, method)

        def sink(table, column):
            references.setdefault((table, column), []).append((relative, lineno))

        if method in _COLUMN_SELECT_METHODS:
            counters["sites_select"] += 1
            if not node.args:
                return  # `.select()` with no projection is `*`
            expr = _constant_str(node.args[0], bound)
            if expr is None:
                counters["skipped"] += 1
                skips.append((site, "select list is not a literal"))
                return
            table, why = resolver.resolve(node)
            if table is None:
                counters["skipped"] += 1
                skips.append((site, why))
                return
            counters["resolved"] += 1
            _parse_select_list(expr, table, sink, skips, site)

        elif method in _COLUMN_FILTER_METHODS:
            counters["sites_filter"] += 1
            if not node.args:
                counters["skipped"] += 1
                skips.append((site, "no positional argument"))
                return
            name = _constant_str(node.args[0], bound)
            if name is None:
                counters["skipped"] += 1
                skips.append((site, "column name is not a literal"))
                return
            table, why = resolver.resolve(node)
            if table is None:
                counters["skipped"] += 1
                skips.append((site, why))
                return
            counters["resolved"] += 1
            attribute_filter(name, table, sink, site)

        elif method in _COLUMN_WRITE_METHODS:
            counters["sites_write"] += 1
            table, why = resolver.resolve(node)
            if table is None:
                counters["skipped"] += 1
                skips.append((site, why))
                return
            payloads = []
            if node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Dict):
                    payloads.append(arg)
                elif isinstance(arg, (ast.List, ast.Tuple)):
                    for element in arg.elts:
                        if isinstance(element, ast.Dict):
                            payloads.append(element)
                        else:
                            counters["skipped_dynamic_payload"] += 1
                            skips.append((site, "list element is not a literal dict"))
                else:
                    counters["skipped_dynamic_payload"] += 1
                    skips.append((site, "payload is not a literal dict"))
            else:
                counters["skipped_dynamic_payload"] += 1
                skips.append((site, "no payload argument"))
            for payload in payloads:
                for key in payload.keys:
                    literal = _constant_str(key, bound)
                    if literal is None:
                        counters["skipped_dynamic_payload"] += 1
                        skips.append((site, "dict key is not a literal"))
                        continue
                    column = _select_base_column(literal)
                    if column:
                        sink(table, column)
            if payloads:
                counters["resolved"] += 1
            conflict = _keyword(node, "on_conflict")
            if conflict is not None:
                literal = _constant_str(conflict, bound)
                if literal is None:
                    counters["skipped"] += 1
                    skips.append((site, "on_conflict is not a literal"))
                else:
                    counters["sites_on_conflict"] += 1
                    for part in literal.split(","):
                        column = _select_base_column(part)
                        if column:
                            sink(table, column)

    def walk_scope(body, resolver, branching=False):
        """One suite, in statement order, sharing one binding map.

        Python scoping is per FUNCTION, so an ``if`` body shares the enclosing
        function's bindings while a nested ``def`` gets a COPY - which is what
        stops one handler's ``query`` reaching another's.
        """
        for stmt in body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                walk_scope(
                    stmt.body, _ColumnChainResolver(resolver.var_tables, bound)
                )
                continue
            for call in _calls_in_statement(stmt):
                visit_call(call, resolver)
            resolver.note_assignment(stmt, branching=branching)
            for field in _SUITE_FIELDS:
                nested = getattr(stmt, field, None)
                if not isinstance(nested, list):
                    continue
                for item in nested:
                    if isinstance(item, ast.excepthandler):
                        walk_scope(item.body, resolver, branching=True)
                    elif isinstance(item, ast.stmt):
                        walk_scope([item], resolver, branching=True)

    walk_scope(tree.body, _ColumnChainResolver(constants=bound))


_COLUMN_SCAN_CACHE: dict = {}


def _column_references():
    """``((table, column) -> [(file, line)], skips, counters)`` under ``backend_app/``.

    Memoised: the scan AST-parses the fifty modules that contain a table verb,
    which is a few seconds, and ten assertions below read it.
    """
    if "scan" not in _COLUMN_SCAN_CACHE:
        root = REPO_ROOT / "backend_app"
        references: dict = {}
        skips: list = []
        counters = {
            "sites_select": 0,
            "sites_filter": 0,
            "sites_write": 0,
            "sites_on_conflict": 0,
            "resolved": 0,
            "skipped": 0,
            "skipped_dynamic_payload": 0,
            "unparsable_files": 0,
            "files": 0,
            "files_without_a_table_verb": 0,
        }
        constants = _ConstantIndex(root, REPO_ROOT)
        for path in sorted(root.rglob("*.py")):
            counters["files"] += 1
            text = path.read_text(encoding="utf-8", errors="replace")
            if ".table(" not in text and ".from_(" not in text:
                # No table verb in the file, so no chain in it can name a
                # relation. Parsing it would yield nothing but skip records
                # for `.update` and `.select` on objects that are not query
                # builders at all - a plain dict's `.update`, for instance.
                counters["files_without_a_table_verb"] += 1
                continue
            tree = constants.tree(path)
            if tree is None:
                counters["unparsable_files"] += 1
                continue
            _scan_module_columns(
                path,
                tree,
                path.relative_to(REPO_ROOT).as_posix(),
                constants.bind(constants.module_name(path)),
                references,
                skips,
                counters,
            )
        _COLUMN_SCAN_CACHE["scan"] = (references, skips, counters)
    return _COLUMN_SCAN_CACHE["scan"]


# --------------------------------------------------------------------------
# the DECLARED oracle: CREATE TABLE plus ALTER TABLE, applied in file order
# --------------------------------------------------------------------------

#: Column-list entries that are TABLE constraints rather than columns.
_TABLE_CONSTRAINT_HEADS = frozenset(
    {"primary", "foreign", "unique", "check", "constraint", "exclude", "like"}
)

#: Words that end a column's TYPE and begin its constraints or default.
_TYPE_TERMINATORS = frozenset(
    {
        "not", "null", "default", "primary", "references", "unique", "check",
        "generated", "collate", "constraint", "deferrable", "on", "storage",
        "compression",
    }
)

#: `ADD <word>` where the word is a constraint keyword, not a column name.
_ADD_IS_NOT_A_COLUMN = frozenset(
    {"constraint", "primary", "foreign", "unique", "check", "exclude", "column"}
)

#: PostgreSQL type spellings that mean the same type. Folded before two
#: declarations are called divergent, so `DECIMAL(10,2)` and `NUMERIC(10,2)`
#: - which are the same type - are not reported as a conflict, while
#: `NUMERIC(10,2)` and `NUMERIC(20,8)` still are.
_TYPE_SYNONYMS = {
    "decimal": "numeric",
    "timestamptz": "timestamp with time zone",
    "timestamp": "timestamp without time zone",
    "int": "integer",
    "int4": "integer",
    "int8": "bigint",
    "bool": "boolean",
    "varchar": "character varying",
    "float8": "double precision",
    "serial": "integer",
    "bigserial": "bigint",
}

_CREATE_TABLE_HEAD = re.compile(
    r"CREATE\s+(?:UNLOGGED\s+|TEMP(?:ORARY)?\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r'(?:"?(?P<schema>[A-Za-z_][A-Za-z0-9_]*)"?\s*\.\s*)?'
    r'"?(?P<name>[A-Za-z_][A-Za-z0-9_]*)"?\s*\(',
    re.IGNORECASE,
)
_ALTER_TABLE_HEAD = re.compile(
    r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?"
    r'(?:"?(?P<schema>[A-Za-z_][A-Za-z0-9_]*)"?\s*\.\s*)?'
    r'"?(?P<name>[A-Za-z_][A-Za-z0-9_]*)"?',
    re.IGNORECASE,
)
_ADD_COLUMN_ACTION = re.compile(
    r"^\s*ADD\s+(?:COLUMN\s+)?(?:IF\s+NOT\s+EXISTS\s+)?"
    r'"?(?P<name>[A-Za-z_][A-Za-z0-9_]*)"?\s+(?P<rest>.*)$',
    re.IGNORECASE | re.DOTALL,
)
_DROP_COLUMN_ACTION = re.compile(
    r'^\s*DROP\s+COLUMN\s+(?:IF\s+EXISTS\s+)?"?(?P<name>[A-Za-z_][A-Za-z0-9_]*)"?',
    re.IGNORECASE,
)
_RENAME_COLUMN_ACTION = re.compile(
    r'^\s*RENAME\s+COLUMN\s+"?(?P<old>[A-Za-z_][A-Za-z0-9_]*)"?'
    r'\s+TO\s+"?(?P<new>[A-Za-z_][A-Za-z0-9_]*)"?',
    re.IGNORECASE,
)
_ALTER_COLUMN_TYPE_ACTION = re.compile(
    r'^\s*ALTER\s+(?:COLUMN\s+)?"?(?P<name>[A-Za-z_][A-Za-z0-9_]*)"?'
    r"\s+(?:SET\s+DATA\s+)?TYPE\s+(?P<type>.*)$",
    re.IGNORECASE | re.DOTALL,
)


def _matching_close_paren(text: str, open_index: int) -> int:
    depth = 0
    for i in range(open_index, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _canonical_type(type_text: str) -> str:
    """Fold synonym spellings; precision and scale are preserved."""
    text = type_text.strip().lower()
    head, _, tail = text.partition("(")
    base = _TYPE_SYNONYMS.get(head.strip(), head.strip())
    return "%s(%s" % (base, tail) if tail else base


def _column_type_text(rest: str) -> str:
    """The type at the head of a column declaration, as written."""
    tokens: list = []
    rest = rest.strip()
    i = 0
    while i < len(rest):
        match = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)", rest[i:])
        if not match:
            break
        word = match.group(1).lower()
        if word in _TYPE_TERMINATORS:
            break
        tokens.append(word)
        i += match.end()
        sized = re.match(r"\s*\(([^()]*)\)", rest[i:])
        if sized:
            tokens[-1] += "(" + re.sub(r"\s+", "", sized.group(1)) + ")"
            i += sized.end()
        array = re.match(r"\s*(?:\[\s*\])+", rest[i:])
        if array:
            tokens[-1] += "[]"
            i += array.end()
        tail = re.match(
            r"\s*(with\s+time\s+zone|without\s+time\s+zone|precision|varying)",
            rest[i:],
            re.IGNORECASE,
        )
        if tail:
            tokens.append(re.sub(r"\s+", " ", tail.group(1).lower()))
            i += tail.end()
            continue
        break
    return " ".join(tokens)


def _create_table_columns(masked: str, open_index: int):
    """``[(column, type_text), ...]`` for the list starting at ``open_index``."""
    close = _matching_close_paren(masked, open_index)
    if close == -1:
        return []
    columns = []
    for entry in _split_at_depth_zero(masked[open_index + 1:close]):
        entry = entry.strip()
        if not entry:
            continue
        head = re.match(r'"?([A-Za-z_][A-Za-z0-9_]*)"?\s*(.*)$', entry, re.DOTALL)
        if not head:
            continue
        name = head.group(1).lower()
        if name in _TABLE_CONSTRAINT_HEADS:
            continue
        columns.append((name, _column_type_text(head.group(2))))
    return columns


_DECLARED_COLUMN_CACHE: dict = {}


def _declared_columns():
    """The migration set's column state, applied in file order.

    Returns ``(columns, type_spellings, created, altered_only)``:

    * ``columns[table]`` - the live set after every CREATE / ADD / DROP /
      RENAME in file order;
    * ``type_spellings[(table, column)]`` - ``{canonical type: {files}}``;
      more than one key is the ``strategy_backtests.version`` shape;
    * ``created`` - tables a ``CREATE TABLE`` in the set declares, so the set
      is AUTHORITATIVE for their columns;
    * ``altered_only`` - tables the set only ``ALTER``s, so its column list
      for them is a SUBSET and cannot be the oracle on its own.

    Parsed off :func:`_mask_sql`'s output, so a ``CREATE TABLE`` quoted inside
    a ``RAISE NOTICE`` is not a declaration. Alembic is deliberately NOT
    consulted - a column declared only there is the drift 13.14 recorded.
    """
    if "declared" not in _DECLARED_COLUMN_CACHE:
        columns: dict = {}
        type_spellings: dict = {}
        created: set = set()
        touched: set = set()

        for path in _sql_migration_files():
            masked = _mask_sql(path.read_text(encoding="utf-8", errors="replace"))

            for match in _CREATE_TABLE_HEAD.finditer(masked):
                if (match.group("schema") or "public").lower() != "public":
                    continue
                table = match.group("name").lower()
                created.add(table)
                bucket = columns.setdefault(table, set())
                for column, type_text in _create_table_columns(
                    masked, match.end() - 1
                ):
                    bucket.add(column)
                    if type_text:
                        type_spellings.setdefault(
                            (table, column), {}
                        ).setdefault(_canonical_type(type_text), set()).add(path.name)

            for match in _ALTER_TABLE_HEAD.finditer(masked):
                if (match.group("schema") or "public").lower() != "public":
                    continue
                table = match.group("name").lower()
                end = masked.find(";", match.end())
                end = len(masked) if end == -1 else end
                for action in _split_at_depth_zero(masked[match.end():end]):
                    add = _ADD_COLUMN_ACTION.match(action)
                    if add and add.group("name").lower() not in _ADD_IS_NOT_A_COLUMN:
                        column = add.group("name").lower()
                        columns.setdefault(table, set()).add(column)
                        touched.add(table)
                        type_text = _column_type_text(add.group("rest"))
                        if type_text:
                            type_spellings.setdefault(
                                (table, column), {}
                            ).setdefault(
                                _canonical_type(type_text), set()
                            ).add(path.name)
                        continue
                    drop = _DROP_COLUMN_ACTION.match(action)
                    if drop:
                        column = drop.group("name").lower()
                        columns.setdefault(table, set()).discard(column)
                        type_spellings.pop((table, column), None)
                        touched.add(table)
                        continue
                    rename = _RENAME_COLUMN_ACTION.match(action)
                    if rename:
                        old = rename.group("old").lower()
                        new = rename.group("new").lower()
                        bucket = columns.setdefault(table, set())
                        bucket.discard(old)
                        bucket.add(new)
                        if (table, old) in type_spellings:
                            type_spellings[(table, new)] = type_spellings.pop(
                                (table, old)
                            )
                        touched.add(table)
                        continue
                    retype = _ALTER_COLUMN_TYPE_ACTION.match(action)
                    if retype:
                        column = retype.group("name").lower()
                        columns.setdefault(table, set()).add(column)
                        touched.add(table)
                        type_text = _column_type_text(retype.group("type"))
                        if type_text:
                            type_spellings.setdefault(
                                (table, column), {}
                            ).setdefault(
                                _canonical_type(type_text), set()
                            ).add(path.name)

        _DECLARED_COLUMN_CACHE["declared"] = (
            columns,
            type_spellings,
            created,
            touched - created,
        )
    return _DECLARED_COLUMN_CACHE["declared"]


def _allowed_columns(table: str):
    """The column set ``table`` is allowed to be read with, or ``None``.

    ``None`` means this guard has no oracle for the table at all, which is the
    TABLE-level guard's business rather than this one's.
    """
    columns, _types, created, _altered = _declared_columns()
    if table in created:
        return frozenset(columns.get(table, ()))
    recorded = PRODUCTION_COLUMNS_FOR_UNCREATED_TABLES.get(table)
    if recorded is not None:
        return frozenset(columns.get(table, set())) | recorded
    return None


# --------------------------------------------------------------------------
# the scanner's own health
# --------------------------------------------------------------------------

#: Every PostgREST form the scanner claims to handle, in the shapes this
#: codebase actually writes them, plus the two that produced real bugs.
#: `ghost_column` is deliberately undeclared so the guard is proven to FIRE.
_COLUMN_SCANNER_FIXTURE = '''
import other_module
from . import listing_projection as _listing_projection

PROJECTION = "id,name," + _listing_projection.LISTING_SELECT

EMBED_PROJECTION = (
    "id,library_id,status,"
    "library_strategies!library_id!inner("
    + _listing_projection.LISTING_SELECT
    + ",marketplace_submissions(submission_state)"
    ")"
)


def one(db, uid):
    # select with every scalar form: alias, cast, json path, count, aggregate,
    # star, and newlines inside the string
    return (
        db.table("outer_table")
        .select(
            """
            plain,
            alias:aliased,
            cast_me::text,
            payload->key,
            payload_two->>other,
            count,
            amount.sum(),
            *
            """
        )
        .eq("filter_one", uid)
        .neq("filter_two", 1)
        .gt("filter_three", 1)
        .gte("filter_four", 1)
        .lt("filter_five", 1)
        .lte("filter_six", 1)
        .like("filter_seven", "%x%")
        .ilike("filter_eight", "%x%")
        .is_("filter_nine", "null")
        .in_("filter_ten", [1])
        .contains("filter_eleven", ["x"])
        .filter("filter_twelve", "eq", 1)
        .order("order_me", desc=True)
        .execute()
    )


def two(db):
    # an embed with a hint recurses into the EMBEDDED table, and the hint is
    # not part of the relation name
    return (
        db.table("outer_table")
        .select("own_column, inner_table!inner(inner_one, inner_two), "
                "labelled:other_inner!fk(other_one)")
        .execute()
    )


def three(db, uid):
    # a chain built across statements, rebound in a branch
    query = db.table("outer_table").select("built_one")
    if uid:
        query = query.eq("built_two", uid)
    return query.execute()


def four(db):
    # two handlers' locals must not leak into one another: this `query` is a
    # DIFFERENT table from `three`'s, and `built_two` must not land here
    query = db.table("second_table").select("second_one")
    return query.eq("second_two", 1).execute()


def five(db, uid, payload):
    db.table("outer_table").insert(
        {"written_one": 1, "written_two": 2}
    ).execute()
    db.table("outer_table").upsert(
        {"upserted_one": 1}, on_conflict="conflict_one,conflict_two"
    ).execute()
    db.table("outer_table").update({"updated_one": 1}).eq("id_col", uid).execute()
    # NOT a literal dict: skipped and counted, never guessed
    db.table("outer_table").insert(payload).execute()
    # NOT a literal table: skipped and counted
    db.table(payload["t"]).select("invisible_one").execute()
    # a dotted filter addresses the EMBEDDED resource, not `outer_table`
    db.table("outer_table").select("x").eq("inner_table.dotted_one", 1).execute()
    # the undeclared one, so the guard is proven to fire rather than to be quiet
    db.table("outer_table").select("ghost_column").execute()
    # a plain dict's `.update` is not a query builder; no table, no column
    {}.update({"not_a_column": 1})
'''

#: What the fixture must resolve to, EXACTLY. `LISTING_SELECT` resolves to
#: nothing here (there is no sibling module to import), so `PROJECTION` and
#: `EMBED_PROJECTION` are unresolvable constants - which is itself part of the
#: claim: an unresolvable constant contributes no column rather than a wrong
#: one.
_COLUMN_FIXTURE_EXPECTED = {
    "outer_table": frozenset(
        {
            "plain", "aliased", "cast_me", "payload", "payload_two", "amount",
            "filter_one", "filter_two", "filter_three", "filter_four",
            "filter_five", "filter_six", "filter_seven", "filter_eight",
            "filter_nine", "filter_ten", "filter_eleven", "filter_twelve",
            "order_me", "own_column", "built_one", "built_two",
            "written_one", "written_two", "upserted_one", "conflict_one",
            "conflict_two", "updated_one", "id_col", "x", "ghost_column",
        }
    ),
    "inner_table": frozenset({"inner_one", "inner_two", "dotted_one"}),
    "other_inner": frozenset({"other_one"}),
    "second_table": frozenset({"second_one", "second_two"}),
}


def _scan_fixture(source: str):
    """Run the scanner over a source string, with no constant index."""
    references: dict = {}
    skips: list = []
    counters = {
        "sites_select": 0,
        "sites_filter": 0,
        "sites_write": 0,
        "sites_on_conflict": 0,
        "resolved": 0,
        "skipped": 0,
        "skipped_dynamic_payload": 0,
    }
    _scan_module_columns(
        None, ast.parse(source), "<fixture>", None, references, skips, counters
    )
    found: dict = {}
    for table, column in references:
        found.setdefault(table, set()).add(column)
    return found, skips, counters


class TestTheColumnScannerIsHonest:
    """Guards the guard. A parser that quietly matches nothing passes
    everything, and a parser drowning in false positives gets allowlisted
    away - both failures look like a green suite."""

    def test_the_fixture_resolves_to_exactly_its_real_columns(self):
        """Every PostgREST form, resolving to an EXACT set.

        If the embed stops being followed, ``inner_table`` goes missing and
        ``inner_table`` appears as a COLUMN of ``outer_table``. If the hint
        stops being stripped, ``other_inner`` becomes ``other_inner!fk``. If
        the scope tracking regresses, ``built_two`` lands on
        ``second_table``. Each of those fails here rather than in a migration
        where the instinct is to reach for the exemption list.
        """
        found, _skips, _counters = _scan_fixture(_COLUMN_SCANNER_FIXTURE)
        assert set(found) == set(_COLUMN_FIXTURE_EXPECTED), (
            "tables: unexpected "
            f"{sorted(set(found) - set(_COLUMN_FIXTURE_EXPECTED))}; missing "
            f"{sorted(set(_COLUMN_FIXTURE_EXPECTED) - set(found))}"
        )
        for table, expected in _COLUMN_FIXTURE_EXPECTED.items():
            assert found[table] == expected, (
                f"{table}: unexpected {sorted(found[table] - expected)}; "
                f"missing {sorted(expected - found[table])}"
            )

    def test_the_fixture_proves_the_guard_still_fires(self):
        """At least one fixture column is deliberately undeclared.

        A scanner that has gone quiet and a schema with no drift look the same
        from the outside. This is the difference.
        """
        found, _skips, _counters = _scan_fixture(_COLUMN_SCANNER_FIXTURE)
        assert "ghost_column" in found["outer_table"], (
            "the deliberately-undeclared fixture column is no longer found, so "
            "nothing proves this guard can still report anything."
        )
        assert _allowed_columns("outer_table") is None, (
            "the fixture's table name has become a real relation; rename the "
            "fixture's tables so the fixture cannot be satisfied by the schema."
        )

    def test_embeds_are_followed_and_never_read_as_columns(self):
        """``rel(a,b)`` is a relation, not a column of the outer table."""
        found, _skips, _counters = _scan_fixture(_COLUMN_SCANNER_FIXTURE)
        for relation in ("inner_table", "other_inner", "marketplace_submissions"):
            assert relation not in found["outer_table"], (
                f"{relation!r} is reported as a COLUMN of outer_table. An "
                "embedded resource names a RELATION; reading it as a column "
                "invents one on the outer table and misses every column inside "
                "the embed."
            )

    def test_the_unresolvable_forms_are_skipped_and_counted(self):
        """A dynamic payload and a dynamic table name are counted, not guessed."""
        found, skips, counters = _scan_fixture(_COLUMN_SCANNER_FIXTURE)
        assert "invisible_one" not in found["outer_table"], (
            "a select whose TABLE is not a literal was attributed to a table "
            "anyway - that is a guess, and a guess is what this counts instead."
        )
        assert counters["skipped"] >= 1 and counters["skipped_dynamic_payload"] >= 1, (
            f"the fixture's unresolvable sites were not counted: {counters}"
        )
        assert any("not a literal" in reason for _site, reason in skips), skips

    def test_the_real_scan_is_non_trivial(self):
        """Floors on the real parse, the way the table-level guard has them."""
        references, _skips, counters = _column_references()
        tables = {table for table, _column in references}
        assert len(references) >= MIN_DISTINCT_COLUMN_PAIRS, (
            f"only {len(references)} distinct table.column pairs parsed out of "
            f"{counters['files']} modules - the scan has stopped matching, so "
            "every assertion built on it is vacuous."
        )
        assert len(tables) >= MIN_TABLES_WITH_COLUMN_REFERENCES, (
            f"only {len(tables)} tables reached by a column reference."
        )
        assert counters["sites_select"] > 100 and counters["sites_filter"] > 300, (
            f"too few call sites parsed: {counters}"
        )

    def test_the_declared_column_oracle_is_non_trivial(self):
        columns, type_spellings, created, altered_only = _declared_columns()
        pairs = sum(len(v) for v in columns.values())
        assert pairs >= MIN_DECLARED_COLUMN_PAIRS, (
            f"only {pairs} declared table.column pairs parsed out of "
            f"{len(_sql_migration_files())} SQL migrations - the column-list "
            "parser has stopped matching."
        )
        assert len(created) > 50, f"only {len(created)} CREATE TABLEs parsed"
        assert altered_only, (
            "no table is ALTER-only any more. 006_reconcile_production_database "
            "exists precisely to ALTER tables no migration creates; if that is "
            "really gone, PRODUCTION_COLUMNS_FOR_UNCREATED_TABLES should go too."
        )
        assert type_spellings, "no column types parsed at all"

    def test_the_alter_half_of_the_oracle_is_load_bearing(self):
        """A CREATE-TABLE-only parse would report these as missing.

        Named rather than counted: these are the columns 006 and the
        later migrations add to tables 001 already created, and they are what
        makes the difference between 83 reported findings and a flood.
        """
        columns, _types, _created, _altered = _declared_columns()
        for table, column, source in (
            ("profiles", "billing_currency", "add_billing_currency_and_dag_hash.sql"),
            ("strategies", "dag_config", "006_reconcile_production_database.sql"),
            ("strategies", "last_signal_at", "015_strategy_last_signal_at.sql"),
            ("library_strategies", "price_minor", "007_marketplace_submissions.sql"),
        ):
            assert column in columns.get(table, set()), (
                f"{table}.{column} is not in the declared set. It is added by an "
                f"ALTER TABLE in {source}, so the oracle has stopped applying "
                "ALTERs and is about to report every such column as missing."
            )

    def test_masking_is_what_the_column_parse_reads(self):
        """A column list inside a string literal is not a declaration."""
        fixture = (
            "DO $$ BEGIN RAISE NOTICE 'CREATE TABLE ghost_decl (ghost_col uuid)'; "
            "END $$;\n"
            "-- CREATE TABLE commented_decl (commented_col uuid);\n"
            "CREATE TABLE real_decl (real_col uuid, other_col numeric(10,2));\n"
        )
        masked = _mask_sql(fixture)
        found = {}
        for match in _CREATE_TABLE_HEAD.finditer(masked):
            found[match.group("name").lower()] = dict(
                _create_table_columns(masked, match.end() - 1)
            )
        assert set(found) == {"real_decl"}, found
        assert found["real_decl"] == {"real_col": "uuid", "other_col": "numeric(10,2)"}


# --------------------------------------------------------------------------
# the general column guard
# --------------------------------------------------------------------------


class TestEveryReferencedColumnIsDeclared:
    """One level deeper than :class:`TestEveryReferencedTableIsDeclared`.

    That class asks whether the RELATION a read names exists. This one asks
    whether the COLUMNS do, which is the question that let creator analytics
    ship a ``.select`` naming ``monthly_price`` and ``rating_average`` off a
    table that has neither.
    """

    def test_every_referenced_column_resolves(self):
        references, _skips, _counters = _column_references()
        offenders: dict = {}
        for (table, column), sites in references.items():
            allowed = _allowed_columns(table)
            if allowed is None:
                continue  # no column oracle for this relation at all
            if column in allowed:
                continue
            if "%s.%s" % (table, column) in KNOWN_UNDECLARED_COLUMN_DEFECTS:
                continue
            offenders[(table, column)] = sites
        if offenders:
            lines = []
            for table, column in sorted(offenders):
                sites = offenders[(table, column)]
                path, lineno = sites[0]
                extra = len(sites) - 1
                suffix = f" (and {extra} more)" if extra else ""
                lines.append(f"  {table}.{column}  at {path}:{lineno}{suffix}")
            pytest.fail(
                "These COLUMNS are named by a read or a write and exist in "
                "neither the migration set nor the recorded production schema, "
                "so PostgREST answers 42703 column does not exist - the same "
                "defect as creator analytics selecting monthly_price off "
                "library_strategies, and as the exchange-list read:\n"
                + "\n".join(lines)
                + "\n\nFix by adding the column in a migration, or by naming the "
                "column the table actually has. Do NOT add the name to "
                "PRODUCTION_COLUMNS_FOR_UNCREATED_TABLES - that set records "
                "columns production ALREADY HAS, and adding one production does "
                "not have re-creates exactly this defect. Do NOT add it to "
                "KNOWN_UNDECLARED_COLUMN_DEFECTS either: that register may only "
                "SHRINK, and every entry in it names a live 42703 that is "
                "waiting on a decision, not a tolerated name."
            )

    def test_the_recorded_production_column_sets_are_still_what_they_claim(self):
        """The roster cannot rot, and cannot shadow a real CREATE TABLE."""
        columns, _types, created, altered_only = _declared_columns()
        references, _skips, _counters = _column_references()
        referenced_tables = {table for table, _column in references}
        for table, recorded in PRODUCTION_COLUMNS_FOR_UNCREATED_TABLES.items():
            assert table not in created, (
                f"{table!r} is now declared by a CREATE TABLE in the migration "
                "set, so the migration set is the authoritative oracle for its "
                "columns. Delete the entry from "
                "PRODUCTION_COLUMNS_FOR_UNCREATED_TABLES rather than leaving a "
                "recorded roster that can disagree with a parsed one."
            )
            assert table in referenced_tables, (
                f"{table!r} is in PRODUCTION_COLUMNS_FOR_UNCREATED_TABLES but "
                "nothing under backend_app/ reads a column of it any more - "
                "remove the entry."
            )
            assert recorded, f"{table!r} has an empty recorded column set"
        assert "users" not in PRODUCTION_COLUMNS_FOR_UNCREATED_TABLES, (
            "users must never be recorded here: there is no public.users in "
            "this schema at all, which is the whole of the health_check defect."
        )
        # EVERY table a column reference reaches must have an oracle, or the
        # guard silently stops covering it. Scoping this to ALTER-only tables
        # was tried and was a hole: `library_ratings` is neither created NOR
        # altered by the migration set - it is declared only in Alembic - so
        # deleting its roster entry left eight columns unchecked and every
        # assertion above still green.
        uncovered = sorted(
            table
            for table in referenced_tables
            if table not in created
            and table not in PRODUCTION_COLUMNS_FOR_UNCREATED_TABLES
        )
        assert not uncovered, (
            f"{uncovered} are read COLUMN BY COLUMN by the application and have "
            "no column oracle at all: no CREATE TABLE in the migration set, and "
            "no recorded production column set. Every column read off them is "
            "unchecked, so this whole section stops covering them silently. "
            "Add the CREATE TABLE - which is the better fix, and the one "
            "TestEveryReferencedTableIsDeclared also wants - or record the "
            "production columns with the server they were read from.\n"
            f"(ALTER-only tables, for context: {sorted(altered_only)})"
        )

    def test_the_two_column_exemption_registers_do_not_overlap(self):
        """A column is either present in production or a known 42703."""
        for key in KNOWN_UNDECLARED_COLUMN_DEFECTS:
            table, column = key.split(".", 1)
            recorded = PRODUCTION_COLUMNS_FOR_UNCREATED_TABLES.get(table)
            if recorded is None:
                continue
            assert column not in recorded, (
                f"{key} is recorded BOTH as present in production and as a "
                "known 42703. The two justifications are incompatible; one of "
                "them is wrong."
            )

    def test_every_known_defect_is_still_a_defect(self):
        """The register is a list of OPEN defects, pinned so it cannot rot.

        An entry that is no longer referenced, or whose column now exists, is
        a FIXED defect and must leave the register - otherwise the register
        becomes a permanent hole in the guard, which is the failure mode the
        table-level exemptions are also pinned against.
        """
        references, _skips, _counters = _column_references()
        stale = []
        for key in sorted(KNOWN_UNDECLARED_COLUMN_DEFECTS):
            table, column = key.split(".", 1)
            if (table, column) not in references:
                stale.append("%s is no longer referenced at all" % key)
                continue
            allowed = _allowed_columns(table)
            if allowed is not None and column in allowed:
                stale.append("%s now exists in the oracle" % key)
        assert not stale, (
            "KNOWN_UNDECLARED_COLUMN_DEFECTS has gone stale:\n  "
            + "\n  ".join(stale)
            + "\n\nRemove the fixed entries. Leaving them makes the register "
            "larger than the defect, and the register is the thing that may "
            "only shrink."
        )

    def test_the_known_defects_are_attributed_rather_than_merely_listed(self):
        """Each entry names the module that owns it and who decides.

        The point of the attribution is that it is checkable: the owning
        module must still exist and must still be the one making the
        reference.
        """
        references, _skips, _counters = _column_references()
        for key, (module, reason) in sorted(
            KNOWN_UNDECLARED_COLUMN_DEFECTS.items()
        ):
            assert reason, f"{key} carries no reason"
            path = REPO_ROOT / "backend_app" / module
            assert path.is_file(), (
                f"{key} is attributed to backend_app/{module}, which is no "
                "longer in the tree. Re-attribute it or remove the entry."
            )
            table, column = key.split(".", 1)
            sites = references.get((table, column), [])
            assert any(
                site.endswith(module) for site, _line in sites
            ), (
                f"{key} is attributed to backend_app/{module} but is referenced "
                f"from {sorted({site for site, _line in sites})} instead."
            )

    def test_the_clone_enrichment_reads_the_table_that_has_the_column(self):
        """``routers/library.py``'s fixed read, pinned both ways.

        ``_enrich_cards_with_user_context`` filtered ``library_strategies`` on
        ``source_library_id``. That table records provenance as
        ``source_strategy_id``; the CLONE is a row in ``strategies``, which is
        where ``clone_strategy`` inserts it with ``source_library_id`` and
        where its own idempotency check reads it back. The old read answered
        ``42703`` on every catalogue page, the ``except`` logged a warning, and
        ``user_has_cloned`` / ``user_rating`` were never set on any card -
        which is the "a read that did not complete must not be collapsed into
        a value" rule, failing in the omission direction.
        """
        right, wrong, column = FIXED_CLONE_ENRICHMENT
        references, _skips, _counters = _column_references()
        library = "backend_app/routers/library.py"

        wrong_sites = [
            (path, line)
            for path, line in references.get((wrong, column), [])
            if path == library
        ]
        assert not wrong_sites, (
            f"{library} reads {wrong}.{column} again at {wrong_sites}. "
            f"{wrong} has no {column} column - production answers "
            f'42703 column "{column}" does not exist. The clone lives in '
            f"{right}; read it there."
        )
        right_sites = [
            (path, line)
            for path, line in references.get((right, column), [])
            if path == library
        ]
        assert len(right_sites) >= 3, (
            f"{library} should read {right}.{column} in at least three places "
            "(the card enrichment, the clone idempotency check and the clone "
            f"insert); found {right_sites}. Without the enrichment read, "
            "user_has_cloned is never set and this class asserts nothing."
        )
        assert column in _allowed_columns(right), (
            f"{right}.{column} has left the oracle; re-establish it before "
            "editing this test."
        )
        assert column not in _allowed_columns(wrong), (
            f"{wrong}.{column} now exists, so the premise of this class has "
            "changed. Re-read it before editing."
        )

    def test_the_eligibility_gate_reads_the_tenant_column_that_exists(self):
        """The 24th defect, fixed at its root rather than registered.

        ``backend/marketplace/eligibility_gate.py``'s read 1 named
        ``tenant_id`` on ``strategies``. Production answers ``42703`` for that
        column (proven in task 13.17, with ``user_id`` on the same table
        answering OK), and the gate turns ANY read failure into
        ``unevaluable=True`` - so the route answered 503 for every marketplace
        submission and nothing could be published. The fix reads the tenant off
        ``user_id``, because tenant == user here; it did NOT add a column to a
        live table, which would have duplicated ``user_id`` and encoded a
        distinction this system does not make.

        Pinned in three directions, so neither the defect nor a cosmetic
        re-registration of it can come back:
        """
        table, absent, present = FIXED_ELIGIBILITY_TENANT_PREDICATE
        references, _skips, _counters = _column_references()
        gate = "backend_app/backend/marketplace/eligibility_gate.py"

        # 1. NOTHING anywhere names the column production does not have.
        absent_sites = references.get((table, absent), [])
        assert not absent_sites, (
            f"{table}.{absent} is referenced again at {absent_sites}. "
            f"public.{table} has no {absent} column - production answers "
            f'42703 column "{absent}" does not exist - and in the gate that '
            "read collapses the whole evaluation to "
            "MARKETPLACE_ELIGIBILITY_UNEVALUABLE (503), which blocks every "
            f"publication. The row's tenant is its {present}."
        )

        # 2. The gate still reads the column that DOES carry the tenant, so
        #    the fix cannot have been "stop reading the strategy row at all".
        present_sites = [
            (path, line)
            for path, line in references.get((table, present), [])
            if path == gate
        ]
        assert len(present_sites) >= 2, (
            f"{gate} should read {table}.{present} at least twice (read 1's "
            f"projection and its owner filter); found {present_sites}. "
            "Without them the ownership and tenant criteria have no column to "
            "decide on and this test asserts nothing."
        )

        # 3. The premise and the register, both ways: the column is still
        #    absent from the oracle, the owner column is still in it, and the
        #    pair is NOT back in the register - a fixed defect that is left
        #    registered makes the register larger than the defect, which is the
        #    one thing it may never be.
        assert present in _allowed_columns(table), (
            f"{table}.{present} has left the oracle; re-establish it before "
            "editing this test."
        )
        assert absent not in _allowed_columns(table), (
            f"{table}.{absent} now exists, so the premise of this test has "
            "changed. Re-read it before editing."
        )
        assert f"{table}.{absent}" not in KNOWN_UNDECLARED_COLUMN_DEFECTS, (
            f"{table}.{absent} is fixed at its root - it must not be in "
            "KNOWN_UNDECLARED_COLUMN_DEFECTS. Re-adding it would turn a closed "
            "defect into a permanent hole in the guard."
        )

    def test_coverage_has_not_eroded(self):
        """The ratchet. Both directions, because either alone is gameable.

        A call site whose table cannot be resolved is not checked at all, so
        the number of them is the size of the blind spot. Left unpinned, a
        refactor that moved every query behind a helper would take the blind
        spot to 100% and leave this whole section green.
        """
        _references, skips, counters = _column_references()
        unresolvable = counters["skipped"] + counters["skipped_dynamic_payload"]
        assert unresolvable == len(skips), (
            f"the skip counters ({unresolvable}) and the skip records "
            f"({len(skips)}) disagree, so one of them is not being kept."
        )
        assert unresolvable <= MAX_UNRESOLVABLE_COLUMN_CALL_SITES, (
            f"{unresolvable} call sites cannot be resolved to a table, up from "
            f"{MAX_UNRESOLVABLE_COLUMN_CALL_SITES}. Every one of them is a "
            "column reference this guard does not check. Either make the new "
            "sites resolvable - a literal table name and a literal projection "
            "are all it takes - or establish why the new blind spot is "
            "acceptable and lower the pin deliberately. Do not raise it to go "
            "green."
        )
        assert counters["resolved"] >= MIN_RESOLVED_COLUMN_CALL_SITES, (
            f"only {counters['resolved']} call sites resolved, down from "
            f"{MIN_RESOLVED_COLUMN_CALL_SITES}. Coverage has shrunk; a parser "
            "that resolves nothing satisfies every assertion above."
        )


# --------------------------------------------------------------------------
# (h) a column declared twice with two different types
#
# This is the `strategy_backtests.version` shape, and it is the one of the
# three recorded defects that actually cost a bug: 001 declares it
# `VARCHAR(20)`, 006 declares it `INTEGER`, so which type a rebuilt database
# gets depends on which file ran last, and the router wrote `"v1.0"` into a
# column production carried as `integer`. Migration 016 reconciles it.
#
# Synonym spellings are folded first - `DECIMAL(10,2)` and `NUMERIC(10,2)` are
# the same type and 16 of the 46 raw hits were only that - so what remains is
# 30 genuine conflicts. They are pinned as an inventory with production's
# actual type recorded beside each, and the inventory may SHRINK and may never
# GROW.
# --------------------------------------------------------------------------

#: 30 columns two migrations declare with two different types, with the type
#: production actually carries. Every one is a rebuild hazard: the type a
#: fresh database gets depends on file order. Recorded as an inventory rather
#: than fixed here - reconciling a type is a data migration against live rows,
#: which is what 016 had to do for `strategy_backtests.version` and is not a
#: parse's decision.
DIVERGENT_COLUMN_TYPES = {
    "referral_codes.code": (
        ("character varying(20)", "character varying(50)"),
        "character varying(50)",
    ),
    "referral_profiles.approved_earnings": (
        ("numeric(10,2)", "numeric(20,8)"),
        "numeric(10,2)",
    ),
    "referral_profiles.id": (("text", "uuid"), "uuid"),
    "referral_profiles.lifetime_earnings": (
        ("numeric(10,2)", "numeric(20,8)"),
        "numeric(10,2)",
    ),
    "referral_profiles.paid_earnings": (
        ("numeric(10,2)", "numeric(20,8)"),
        "numeric(10,2)",
    ),
    "referral_profiles.pending_earnings": (
        ("numeric(10,2)", "numeric(20,8)"),
        "numeric(10,2)",
    ),
    "referral_profiles.referral_code": (
        ("character varying(20)", "character varying(50)"),
        "character varying(50)",
    ),
    "strategy_backtests.commission": (
        ("numeric(10,6)", "numeric(6,4)"),
        "numeric(6,4)",
    ),
    "strategy_backtests.dataset": (("character varying(100)", "text"), "text"),
    "strategy_backtests.end_date": (
        ("date", "timestamp with time zone"),
        "timestamp with time zone",
    ),
    "strategy_backtests.id": (("text", "uuid"), "text"),
    "strategy_backtests.initial_capital": (
        ("numeric(15,2)", "numeric(20,8)"),
        "numeric(15,2)",
    ),
    "strategy_backtests.profit_factor": (
        ("numeric(10,4)", "numeric(8,4)"),
        "numeric(8,4)",
    ),
    "strategy_backtests.sharpe_ratio": (
        ("numeric(10,4)", "numeric(8,4)"),
        "numeric(8,4)",
    ),
    "strategy_backtests.slippage": (
        ("numeric(10,6)", "numeric(6,4)"),
        "numeric(6,4)",
    ),
    "strategy_backtests.sortino_ratio": (
        ("numeric(10,4)", "numeric(8,4)"),
        "numeric(8,4)",
    ),
    "strategy_backtests.start_date": (
        ("date", "timestamp with time zone"),
        "timestamp with time zone",
    ),
    "strategy_backtests.status": (
        ("character varying(20)", "character varying(50)"),
        "character varying(50)",
    ),
    "strategy_backtests.strategy_id": (("text", "uuid"), "text"),
    "strategy_backtests.total_return": (
        ("numeric(15,2)", "numeric(20,8)"),
        "numeric(15,2)",
    ),
    "strategy_backtests.user_id": (("text", "uuid"), "text"),
    "strategy_backtests.version": (
        ("character varying(20)", "integer"),
        "character varying(20)",
    ),
    "strategy_backtests.win_rate": (
        ("numeric(10,4)", "numeric(6,4)"),
        "numeric(6,4)",
    ),
    "strategy_deployments.exchange_id": (
        ("character varying(50)", "uuid"),
        "character varying(50)",
    ),
    "strategy_research_reports.id": (("text", "uuid"), "text"),
    "strategy_research_reports.overall_quality_score": (
        ("numeric(5,4)", "numeric(6,4)"),
        "numeric(6,4)",
    ),
    "strategy_research_reports.strategy_id": (("text", "uuid"), "text"),
    "strategy_research_reports.strategy_score": (
        ("jsonb", "numeric(6,4)"),
        "numeric(6,4)",
    ),
    "strategy_research_reports.user_id": (("text", "uuid"), "text"),
    "strategy_research_reports.warnings": (("jsonb", "text[]"), "jsonb"),
}

#: The one divergence that has been RECONCILED, and the migration that did it.
#: 016 restores `VARCHAR(20)` after 006 made it `INTEGER`; the router writes
#: `"v1.0"`, which an integer column rejects.
RECONCILED_DIVERGENCE = (
    "strategy_backtests",
    "version",
    "character varying(20)",
    "016_strategy_backtests_version_label.sql",
)


def _divergent_column_types():
    """``{"table.column": {canonical type: {files}}}`` for every conflict."""
    _columns, type_spellings, _created, _altered = _declared_columns()
    return {
        "%s.%s" % key: value
        for key, value in type_spellings.items()
        if len(value) > 1
    }


class TestColumnTypesAreDeclaredOnce:
    """Two migrations declaring one column with two types is a rebuild
    hazard: the type a fresh database gets depends on file order."""

    def test_the_synonym_folding_is_what_makes_this_tractable(self):
        """``DECIMAL(10,2)`` and ``NUMERIC(10,2)`` are one type; the fold must
        not also swallow a precision change, which is a real conflict."""
        assert _canonical_type("DECIMAL(10,2)") == _canonical_type("numeric(10,2)")
        assert _canonical_type("TIMESTAMPTZ") == _canonical_type(
            "timestamp with time zone"
        )
        assert _canonical_type("VARCHAR(20)") == "character varying(20)"
        assert _canonical_type("numeric(10,2)") != _canonical_type("numeric(20,8)"), (
            "a precision change is a real divergence - 20,8 and 10,2 round "
            "money differently - and must not be folded away."
        )
        assert _canonical_type("jsonb") != _canonical_type("text[]")

    def test_the_type_parser_reads_the_shapes_this_set_contains(self):
        for declaration, expected in (
            ("VARCHAR(20) NOT NULL DEFAULT 'v1.0'", "varchar(20)"),
            ("NUMERIC(20, 8) DEFAULT 0", "numeric(20,8)"),
            ("TIMESTAMP WITH TIME ZONE DEFAULT NOW()", "timestamp with time zone"),
            ("DOUBLE PRECISION", "double precision"),
            ("TEXT[] DEFAULT '{}'", "text[]"),
            ("UUID PRIMARY KEY REFERENCES other(id)", "uuid"),
            ("JSONB NOT NULL", "jsonb"),
            ("BOOLEAN DEFAULT FALSE", "boolean"),
        ):
            assert _column_type_text(declaration) == expected, (
                f"{declaration!r} parsed as "
                f"{_column_type_text(declaration)!r}, expected {expected!r}"
            )

    def test_the_divergence_inventory_has_not_grown(self):
        found = _divergent_column_types()
        new = sorted(set(found) - set(DIVERGENT_COLUMN_TYPES))
        if new:
            lines = []
            for key in new:
                spellings = found[key]
                detail = "; ".join(
                    "%s in %s" % (type_text, sorted(files))
                    for type_text, files in sorted(spellings.items())
                )
                lines.append(f"  {key}: {detail}")
            pytest.fail(
                "These columns are now declared with two different types by "
                "two different migrations, so the type a rebuilt database gets "
                "depends on which file ran last. That is the "
                "strategy_backtests.version shape: 001 said VARCHAR(20), 006 "
                "said INTEGER, production carried integer, and the router "
                'wrote "v1.0" into it:\n'
                + "\n".join(lines)
                + "\n\nDeclare the column once, or add an explicit "
                "ALTER COLUMN ... TYPE reconciliation the way "
                "016_strategy_backtests_version_label.sql does. Adding the "
                "name to DIVERGENT_COLUMN_TYPES re-creates the defect: that "
                "inventory records conflicts that already shipped and may only "
                "SHRINK."
            )

    def test_the_divergence_inventory_has_not_gone_stale(self):
        """An entry that is no longer divergent has been fixed; it must leave.

        Without this the inventory outlives the conflicts and this class stops
        being able to tell the difference between a clean set and a parser that
        has stopped matching.
        """
        found = _divergent_column_types()
        fixed = sorted(set(DIVERGENT_COLUMN_TYPES) - set(found))
        assert not fixed, (
            f"{fixed} are no longer declared with conflicting types. Remove "
            "them from DIVERGENT_COLUMN_TYPES - the inventory is the thing "
            "that may only shrink, and an entry that outlives its conflict is "
            "a permanent hole."
        )

    def test_each_recorded_conflict_still_names_two_real_spellings(self):
        found = _divergent_column_types()
        for key, (spellings, _production) in sorted(
            DIVERGENT_COLUMN_TYPES.items()
        ):
            actual = tuple(sorted(found[key]))
            assert actual == tuple(sorted(spellings)), (
                f"{key} now diverges as {actual}, not as {tuple(sorted(spellings))}. "
                "The conflict has changed shape; re-read it rather than "
                "updating the tuple to match."
            )

    def test_the_one_reconciled_divergence_stays_reconciled(self):
        """016's reconciliation, pinned at the column-type level.

        ``tests/test_backtest_version_label_regression.py`` pins the
        statement; this pins the OUTCOME of applying the whole set in order,
        which is the thing a rebuilt database actually gets.
        """
        table, column, expected, migration = RECONCILED_DIVERGENCE
        columns, type_spellings, _created, _altered = _declared_columns()
        assert column in columns.get(table, set()), (
            f"{table}.{column} is not in the declared set at all."
        )
        spellings = type_spellings.get((table, column), {})
        assert expected in spellings, (
            f"{table}.{column} is no longer declared as {expected!r} anywhere; "
            f"spellings found: {sorted(spellings)}. {migration} exists to "
            "restore that type after 006 made it integer, and the router writes "
            'a label like "v1.0" which an integer column rejects.'
        )
        assert migration in spellings[expected], (
            f"{migration} no longer declares {table}.{column} as {expected!r}; "
            f"the type comes from {sorted(spellings[expected])} instead. If "
            "that migration has been renamed, update "
            "RECONCILED_DIVERGENCE deliberately."
        )
