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
