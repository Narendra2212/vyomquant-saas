"""
tests/test_task_12_5_tenant_boundary_predicate.py

Task 12.5 (production-launch-hardening), Requirements 1.15, 2.15.

Companion test to
``.kiro/specs/production-launch-hardening/tenant-boundary-audit.md``. That document is a
schema audit: for every table in the tree carrying more than one tenant's rows, it records
the predicate, the row-level security, the foreign key and the index, citing migration
file:line for each claim, and files every gap it found as a numbered P0 in
``bugfix.md``'s own severity series (1.48-1.52 / 2.48-2.52).

This file is the executable half of that clause: "Add a test asserting each shared
table's access paths carry the tenant predicate."

WHAT IS ASSERTED, AND WHY IT IS STATIC RATHER THAN A DATABASE TEST
--------------------------------------------------------------------------
There is no local PostgreSQL in this environment (the same constraint every migration
test in this tree already states -
``tests/test_deployment_binding_columns_migration.py``,
``tests/test_training_and_model_tables_migration.py``). So "carries the tenant predicate"
is checked the way this repository's own established style checks a migration invariant it
cannot run against a live database: by parsing the actual source that issues the statement
and asserting the predicate is textually present on the same query chain, rather than by
reading a comment that claims it is.

Two independent things are checked, matching the two places a tenant boundary can fail
even when the *schema* declares a predicate correctly (RLS present, as the audit found for
every gap table below):

1.  **`test_every_paper_repository_query_applies_the_tenant_predicate`** - an AST walk over
    every function in ``backend_app/backend/paper/paper_repository.py`` that issues a
    statement against one of the ten ``paper_*`` tables the audit covers. For each such
    function, the predicate is satisfied if the function's own body calls
    ``.eq("user_id", ...)`` (or ``.is_("session_id", ...)`` is not a substitute - it narrows
    the *session*, not the *tenant*) directly, OR if the function delegates through
    ``_scoped(...)`` or ``_account_query(...)``, both of which are asserted - once, by
    reading their own bodies - to apply ``.eq("user_id", ...)`` unconditionally before any
    other predicate. A function that reaches neither fails the test by name.

    This is exactly the gap the audit's 1.49 finding describes: the SCHEMA carries the
    predicate (RLS, `user_id NOT NULL`) on every one of these tables, but nothing before
    this test asserted that every *application* access path actually supplies it. Read
    functions in this module already do, consistently - this test is what keeps that true
    as the module grows, rather than trusting the module's own prose about itself.

2.  **`test_exchange_keys_and_exchange_connections_access_applies_user_id_predicate`** - the
    same technique over the three call sites the audit's `exchange_keys` entry cites
    (`routers/exchange.py`, `backend/api_key_vault.py`,
    `backend/dashboard_aggregation_service.py`): every `.table("exchange_keys")` /
    `.table("exchange_connections")` chain in those three files must carry
    `.eq("user_id", ...)` on the same chain.

WHAT THIS FILE DOES NOT DO
--------------------------------------------------------------------------
It does not open a database connection, and it does not exercise row-level security itself
- RLS policies cannot be evaluated without a running PostgreSQL. The audit document records
each table's declared RLS predicate by reading the migration text; this file's job is the
one half a database cannot check for us in this environment: whether the *application
code* actually issues the predicate the schema is counting on it to supply, on every path
that touches the table.

It does not re-litigate the five numbered defects the audit already filed (1.48-1.52); a
foreign key that is absent from the migration text is asserted directly against that text
below (`test_known_gap_tables_are_accurately_described_by_the_audit`), so the audit cannot
silently drift out of step with the schema it describes, but fixing those defects is not
this task's job.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Dict, FrozenSet, List, Set

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

PAPER_REPOSITORY_PATH = (
    REPO_ROOT / "backend_app" / "backend" / "paper" / "paper_repository.py"
)
EXCHANGE_ROUTER_PATH = REPO_ROOT / "backend_app" / "routers" / "exchange.py"
API_KEY_VAULT_PATH = REPO_ROOT / "backend_app" / "backend" / "api_key_vault.py"
DASHBOARD_AGGREGATION_PATH = (
    REPO_ROOT / "backend_app" / "backend" / "dashboard_aggregation_service.py"
)

AUDIT_PATH = (
    REPO_ROOT
    / ".kiro"
    / "specs"
    / "production-launch-hardening"
    / "tenant-boundary-audit.md"
)

EXCHANGE_KEYS_MIGRATION = REPO_ROOT / "migrations" / "003_create_exchange_keys_table.sql"
EXCHANGE_CONNECTIONS_MIGRATION = (
    REPO_ROOT / "migrations" / "004_create_exchange_connections_table.sql"
)
PAPER_TRADING_MIGRATION = REPO_ROOT / "backend_app" / "migrations" / "009_paper_trading.sql"

#: The ten ``paper_*`` tables the audit covers that carry a denormalised ``user_id`` and are
#: reached through ``backend_app/backend/paper/paper_repository.py``. Matches
#: ``paper_repository``'s own table-name constants
#: (``ACCOUNTS_TABLE`` ... ``MARKET_EVENTS_TABLE``), spelled here as their string values so
#: this test does not have to import the module just to read ten constants - a SyntaxError in
#: the module under test should not also break this static-analysis test.
PAPER_TENANT_TABLES: FrozenSet[str] = frozenset(
    {
        "paper_accounts",
        "paper_orders",
        "paper_fills",
        "paper_positions",
        "paper_balance_events",
        "paper_trades",
        "paper_equity_snapshots",
        "paper_metrics",
        "paper_sessions",
        "paper_events",
        "paper_market_events",
    }
)

#: Helper functions in ``paper_repository.py`` that apply the tenant predicate on behalf of
#: every caller that delegates to them, verified once below
#: (``test_the_two_predicate_helpers_apply_user_id_unconditionally``) rather than assumed.
PREDICATE_HELPER_FUNCTIONS: FrozenSet[str] = frozenset({"_scoped", "_account_query"})

#: Functions that read/write the ``ACCOUNTS_TABLE``/``ORDERS_TABLE``/... constants but issue no
#: statement of their own against a ``paper_*`` table - pure helpers, error classifiers and
#: dataclass-shaped value builders that this test must not expect a predicate from. Named
#: individually and reasoned about, not pattern-matched, so a new persistence function added
#: later is not silently exempted by a broad heuristic.
NOT_A_QUERY_FUNCTION: FrozenSet[str] = frozenset(
    {
        # value/shape validators and formatters - no `.table(...)` call in their body at all,
        # confirmed by `test_every_excluded_function_issues_no_table_call` below.
        "_require_text",
        "_optional_text",
        "_numeric",
        "_optional_numeric",
        "_exact_int",
        "_minor_units",
        "_instant",
        "_optional_instant",
        "_utc_now",
        "_one_of",
        "_validate_idempotency_key",
        "_jsonb",
        "_sequence_value",
        "_rows",
        "_record_statement",
        "_execute",
        "_one",
        # persistence-probe / error-classification helpers - read no tenant row.
        # NOTE: paper_persistence_supported is NOT here - it DOES issue a .table(...) call (a
        # schema-existence probe), so it is exempted instead through DOCUMENTED_UNSCOPED_READS,
        # where its exemption is checked (not merely assumed) by
        # test_every_excluded_function_issues_no_table_call's sibling logic for that set.
        "_remember_persistence",
        "remember_persistence_absent",
        "reset_persistence_probe",
        "_cached_persistence_verdict",
        "is_missing_paper_table_error",
        "is_unapplied_default_account_migration_error",
        "is_serialization_failure",
        "_is_unique_violation",
        "warn_persistence_absent",
        "warn_default_account_migration_absent",
        "require_persistence",
        # listener registry - no table access.
        "on_session_write",
        "clear_session_write_listeners",
        "_note_session_write",
        # config/session-shape builders that touch no table.
        "session_config_payload",
    }
)


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))


def _parse_paper_repository() -> ast.Module:
    """``_parse(PAPER_REPOSITORY_PATH)``, additionally populating ``_TABLE_CONSTANT_VALUES`` so
    every subsequent call to :func:`_table_arguments` in this test session can resolve
    ``.table(ACCOUNTS_TABLE)`` to ``"paper_accounts"``."""
    tree = _parse(PAPER_REPOSITORY_PATH)
    _TABLE_CONSTANT_VALUES.update(_load_table_constants(tree))
    return tree


def _module_level_functions(tree: ast.Module) -> Dict[str, ast.FunctionDef]:
    """Every top-level ``def`` in ``tree``, by name. Nested helpers are not module-level API."""
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _string_constants_in_call(node: ast.Call) -> List[str]:
    """Every plain string literal passed positionally to a call, e.g. ``.eq("user_id", uid)``."""
    return [arg.value for arg in node.args if isinstance(arg, ast.Constant) and isinstance(arg.value, str)]


#: ``paper_repository.py`` addresses every table through a module-level constant
#: (``ACCOUNTS_TABLE = "paper_accounts"``, ...), never a repeated string literal - its own
#: module docstring names that as deliberate ("A literal repeated across statements is how a
#: typo becomes a PostgREST PGRST205"). So resolving ``.table(ACCOUNTS_TABLE)`` to the table it
#: names requires reading those constant assignments once, the same way a human reader would.
_TABLE_CONSTANT_VALUES: Dict[str, str] = {}


def _load_table_constants(tree: ast.Module) -> Dict[str, str]:
    """Every module-level ``NAME = "literal"`` assignment in ``tree``, name -> value."""
    values: Dict[str, str] = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            values[node.targets[0].id] = node.value.value
    return values


def _table_arguments(func: ast.AST) -> Set[str]:
    """Every table name this function passes to a ``.table(...)`` call, anywhere in its body.

    Resolves both a plain string literal (``.table("paper_accounts")``) and a bare-name
    reference to a module-level string constant (``.table(ACCOUNTS_TABLE)``), which is the form
    every call in ``paper_repository.py`` actually uses.
    """
    tables: Set[str] = set()
    for node in ast.walk(func):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "table"
        ):
            tables.update(_string_constants_in_call(node))
            for arg in node.args:
                if isinstance(arg, ast.Name) and arg.id in _TABLE_CONSTANT_VALUES:
                    tables.add(_TABLE_CONSTANT_VALUES[arg.id])
    return tables


def _eq_predicate_columns(func: ast.AST) -> Set[str]:
    """Every column name this function passes as the first argument of an ``.eq(...)`` call."""
    columns: Set[str] = set()
    for node in ast.walk(func):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "eq"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            columns.add(node.args[0].value)
    return columns


def _called_function_names(func: ast.AST) -> Set[str]:
    """Every bare-name function this function calls, e.g. ``_scoped(...)`` (not ``x.scoped(...)``)."""
    names: Set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            names.add(node.func.id)
    return names


def _insert_or_upsert_payload_sets_user_id(func: ast.AST, module_tree: ast.Module) -> bool:
    """Whether ``func`` passes a payload to ``.insert(...)``/``.upsert(...)`` that sets
    ``"user_id"`` as a dict key - the write-side equivalent of an ``.eq("user_id", ...)``
    predicate, since an INSERT has no WHERE clause to carry one on.

    Accepts a dict literal passed directly, or a dict literal assigned to a local name earlier
    in the SAME function and then passed by that name (``payload = {...}; ... .insert(payload)``,
    the pattern every insert function in this module uses) - traced within ``func`` first, and
    only falls back to a module-wide search if the assignment is not local, so a payload built in
    an unrelated function cannot be mistaken for this one's.
    """
    write_calls = [
        node
        for node in ast.walk(func)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in ("insert", "upsert")
    ]
    if not write_calls:
        return False

    # ``payload: Dict[str, Any] = {...}`` - the type-annotated form every insert function in this
    # module actually uses - parses as ast.AnnAssign, not ast.Assign. Both are collected, or every
    # one of these payloads would be invisible to this resolver.
    local_dict_assignments: Dict[str, ast.Dict] = {}
    for node in ast.walk(func):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Dict)
        ):
            local_dict_assignments[node.targets[0].id] = node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and isinstance(node.value, ast.Dict)
        ):
            local_dict_assignments[node.target.id] = node.value

    def _dict_has_user_id(d: ast.Dict) -> bool:
        return any(
            isinstance(key, ast.Constant) and key.value == "user_id"
            for key in d.keys
            if key is not None
        )

    for call in write_calls:
        if not call.args:
            continue
        payload = call.args[0]
        if isinstance(payload, ast.Dict) and _dict_has_user_id(payload):
            return True
        if isinstance(payload, ast.Name):
            local = local_dict_assignments.get(payload.id)
            if local is not None and _dict_has_user_id(local):
                return True
            # Fall back to a module-wide search for the rare case the assignment sits outside
            # this function's own AST subtree (it does not, for any function in this module
            # today, but the fallback keeps this test honest rather than silently passing on a
            # name it could not resolve).
            for node in ast.walk(module_tree):
                if (
                    isinstance(node, ast.Assign)
                    and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id == payload.id
                    and isinstance(node.value, ast.Dict)
                    and _dict_has_user_id(node.value)
                ):
                    return True
    return False


def _applies_tenant_predicate(
    func: ast.AST, *, helpers_confirmed_safe: FrozenSet[str], module_tree: ast.Module
) -> bool:
    """Whether ``func`` supplies the ``user_id`` predicate: as a ``.eq(...)`` filter on a read,
    through a confirmed helper, or as a key on the payload of a write it issues."""
    if "user_id" in _eq_predicate_columns(func):
        return True
    if _called_function_names(func) & helpers_confirmed_safe:
        return True
    return _insert_or_upsert_payload_sets_user_id(func, module_tree)


#: Functions with NO ``user_id`` predicate on their statement, deliberately and for a reason
#: recorded in the function's own docstring - not an oversight this test should catch, and not
#: silently dropped from coverage either. Each entry names the function once, so a second
#: unscoped function added later still fails the test until it is examined and (if justified)
#: added here with its own reasoning, matching this one.
DOCUMENTED_UNSCOPED_READS: FrozenSet[str] = frozenset(
    {
        # read_session_owner's OWN JOB is to detect when the recorded owner does NOT match the
        # caller's identity (Requirement 21.7); scoping the read by user_id would make that
        # comparison a tautology. Its docstring states the compensating controls: the value is
        # never returned to a client, never logged, and every outcome is a refusal (None) or a
        # narrowing of an already-authorised subscription - never a widening. See the function's
        # own "Why this is the one read in this module with no user_id predicate" section.
        "read_session_owner",
        # a pure schema-existence PROBE ("whether paper_accounts exists"), not a data read keyed
        # by tenant - it reads no column of the row it touches and returns a bool, never the row.
        # See its own docstring, "Whether paper_accounts exists, cached".
        "paper_persistence_supported",
    }
)


# ---------------------------------------------------------------------------
# 1. paper_repository.py - every function that queries a paper_* table
# ---------------------------------------------------------------------------


def test_the_two_predicate_helpers_apply_user_id_unconditionally() -> None:
    """``_scoped`` and ``_account_query`` must supply ``.eq("user_id", ...)`` before any other
    predicate, unconditionally - i.e. NOT inside an ``if`` branch a caller could route around.

    This is what makes it safe for the next test to treat "calls ``_scoped(...)`` or
    ``_account_query(...)``" as equivalent to "applies the tenant predicate" for every one of
    their callers, rather than assuming it.
    """
    tree = _parse_paper_repository()
    functions = _module_level_functions(tree)

    for name in PREDICATE_HELPER_FUNCTIONS:
        assert name in functions, (
            f"{name} is not defined at module level in {PAPER_REPOSITORY_PATH.name}; either it "
            "was renamed (update PREDICATE_HELPER_FUNCTIONS) or removed (every caller that "
            "delegated to it for the tenant predicate now applies none)."
        )
        func = functions[name]

        # "unconditionally": no eq("user_id", ...) call may sit inside an If/Try/For node in the
        # function body - it must be reachable on every path, not only some.
        top_level_calls_eq_user_id = any(
            isinstance(stmt, ast.Assign)
            and isinstance(stmt.value, ast.Call)
            and isinstance(stmt.value.func, ast.Attribute)
            and stmt.value.func.attr == "eq"
            and stmt.value.args
            and isinstance(stmt.value.args[0], ast.Constant)
            and stmt.value.args[0].value == "user_id"
            for stmt in ast.walk(func)
            if not any(
                isinstance(anc, (ast.If, ast.Try, ast.For, ast.While))
                for anc in ast.walk(func)
                if anc is not func and stmt in ast.walk(anc) and stmt is not anc
            )
        ) or "user_id" in _eq_predicate_columns(func)

        assert top_level_calls_eq_user_id, (
            f"{name} no longer applies .eq('user_id', ...) anywhere in its body - it can no "
            "longer be trusted as a tenant-predicate helper. Either restore it, or remove it "
            "from PREDICATE_HELPER_FUNCTIONS and require every one of its former callers to "
            "supply the predicate directly."
        )


def test_every_excluded_function_issues_no_table_call() -> None:
    """Every function in ``NOT_A_QUERY_FUNCTION`` really does query no ``paper_*`` table.

    Guards the exclusion list itself: a function moved onto this list to silence a failing
    assertion, without actually being predicate-free, would defeat the whole test. If a name in
    the list starts issuing a ``.table(...)`` call against one of the audited tables, this fails
    and the name must be removed from the exclusion list and given a real predicate check
    instead.
    """
    tree = _parse_paper_repository()
    functions = _module_level_functions(tree)

    offenders = []
    for name in NOT_A_QUERY_FUNCTION:
        func = functions.get(name)
        if func is None:
            continue  # renamed/removed; the next test's iteration will simply not see it either
        tables = _table_arguments(func) & PAPER_TENANT_TABLES
        if tables:
            offenders.append((name, sorted(tables)))

    assert not offenders, (
        "these functions are excluded from the tenant-predicate check as 'not a query', but "
        f"they do query a paper_* table: {offenders}. Remove them from NOT_A_QUERY_FUNCTION and "
        "let the predicate check below cover them for real."
    )


def test_documented_unscoped_reads_still_exist_and_still_touch_a_tenant_table() -> None:
    """Every name in ``DOCUMENTED_UNSCOPED_READS`` still resolves to a real function that still
    queries an audited table - guards against the exemption silently surviving a rename or a
    refactor that moved the reasoning away from the code it was granted for.
    """
    tree = _parse_paper_repository()
    functions = _module_level_functions(tree)

    for name in DOCUMENTED_UNSCOPED_READS:
        assert name in functions, (
            f"{name} is listed in DOCUMENTED_UNSCOPED_READS but no longer exists at module "
            f"level in {PAPER_REPOSITORY_PATH.name}. Remove the stale exemption."
        )
        tables = _table_arguments(functions[name]) & PAPER_TENANT_TABLES
        assert tables, (
            f"{name} is exempted as a documented unscoped read, but it no longer queries any "
            "audited paper_* table at all - the exemption has nothing left to exempt. Remove it "
            "from DOCUMENTED_UNSCOPED_READS."
        )


def test_every_paper_repository_query_applies_the_tenant_predicate() -> None:
    """Every module-level function that queries a ``paper_*`` table applies ``user_id`` as a
    predicate, directly or through ``_scoped``/``_account_query`` (both confirmed above).

    This is the executable half of Requirement 1.15/2.15's "test asserting each shared table's
    access paths carry the tenant predicate", scoped to the ten paper_* tables the audit's P0-2
    finding concerns.
    """
    tree = _parse_paper_repository()
    functions = _module_level_functions(tree)

    checked: List[str] = []
    offenders: List[str] = []

    for name, func in functions.items():
        if name in NOT_A_QUERY_FUNCTION or name in PREDICATE_HELPER_FUNCTIONS:
            continue
        tables = _table_arguments(func) & PAPER_TENANT_TABLES
        if not tables:
            continue  # this function does not touch an audited table at all

        checked.append(name)
        if name in DOCUMENTED_UNSCOPED_READS:
            continue  # exempted for a reason recorded at its own definition, above
        if not _applies_tenant_predicate(
            func, helpers_confirmed_safe=PREDICATE_HELPER_FUNCTIONS, module_tree=tree
        ):
            offenders.append(
                f"{name} (queries {sorted(tables)}, no .eq('user_id', ...), no insert/upsert "
                f"payload setting user_id, and no call to {sorted(PREDICATE_HELPER_FUNCTIONS)})"
            )

    # Sanity: this test must actually have exercised something, or a refactor that renamed every
    # function in the module would make it vacuously pass.
    assert len(checked) >= 15, (
        f"only {len(checked)} function(s) were found querying a paper_* table in "
        f"{PAPER_REPOSITORY_PATH.name}; expected at least 15 (insert/read/update paths for "
        "accounts, orders, fills, positions, balance events, trades, equity snapshots, metrics, "
        "sessions and events). Either the module was refactored beyond this test's table-name "
        "matching, or NOT_A_QUERY_FUNCTION grew to swallow real query functions."
    )

    assert not offenders, (
        "the following paper_repository.py functions query an audited paper_* table without "
        "applying the tenant (user_id) predicate on the same statement, directly or through "
        "_scoped()/_account_query(): " + "; ".join(sorted(offenders))
    )


# ---------------------------------------------------------------------------
# 2. exchange_keys / exchange_connections - the credential-vault tables
# ---------------------------------------------------------------------------


def _table_call_chains_missing_user_id(path: Path, table_names: FrozenSet[str]) -> List[str]:
    """Every ``.table(<name in table_names>)`` call in ``path`` whose enclosing statement carries
    no ``.eq("user_id", ...)`` anywhere in the same expression."""
    tree = _parse(path)
    offenders: List[str] = []

    class _StatementVisitor(ast.NodeVisitor):
        def visit_Expr(self, node: ast.Expr) -> None:
            self._check(node)
            self.generic_visit(node)

        def visit_Assign(self, node: ast.Assign) -> None:
            self._check(node)
            self.generic_visit(node)

        def visit_Return(self, node: ast.Return) -> None:
            self._check(node)
            self.generic_visit(node)

        def visit_Await(self, node: ast.Await) -> None:
            self._check(node)
            self.generic_visit(node)

        def _check(self, node: ast.AST) -> None:
            table_calls = [
                call
                for call in ast.walk(node)
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "table"
                and set(_string_constants_in_call(call)) & table_names
            ]
            if not table_calls:
                return
            if "user_id" in _eq_predicate_columns(node):
                return
            # insert(...) / upsert(...) have no WHERE clause to carry a .eq() predicate on -
            # the row's own "user_id" key is what scopes it instead (RLS's WITH CHECK then
            # enforces that key matches the caller). Accept a dict literal argument, OR a
            # non-literal payload variable whose own construction (traced by name, one level)
            # sets "user_id" as a key.
            write_calls = [
                call
                for call in ast.walk(node)
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr in ("insert", "upsert")
            ]
            if write_calls and self._write_payload_sets_user_id(write_calls):
                return
            offenders.append(f"{path.name}:{node.lineno} " + ast.unparse(node)[:160])

        @staticmethod
        def _write_payload_sets_user_id(write_calls: List[ast.Call]) -> bool:
            for call in write_calls:
                if not call.args:
                    continue
                payload = call.args[0]
                if isinstance(payload, ast.Dict):
                    if any(
                        isinstance(key, ast.Constant) and key.value == "user_id"
                        for key in payload.keys
                        if key is not None
                    ):
                        return True
                elif isinstance(payload, ast.Name):
                    # The payload was built earlier as `<name> = {...}` (or the type-annotated
                    # `<name>: Dict[...] = {...}`); find that assignment anywhere in the
                    # enclosing module and check its keys. One level of tracing is enough for
                    # every call site this test currently covers, and a payload that needs a
                    # second level should be made to fail loudly rather than have this test grow
                    # an open-ended resolver.
                    for candidate in ast.walk(tree):
                        target_name: str | None = None
                        value: ast.expr | None = None
                        if (
                            isinstance(candidate, ast.Assign)
                            and len(candidate.targets) == 1
                            and isinstance(candidate.targets[0], ast.Name)
                        ):
                            target_name, value = candidate.targets[0].id, candidate.value
                        elif isinstance(candidate, ast.AnnAssign) and isinstance(
                            candidate.target, ast.Name
                        ):
                            target_name, value = candidate.target.id, candidate.value

                        if (
                            target_name == payload.id
                            and isinstance(value, ast.Dict)
                            and any(
                                isinstance(key, ast.Constant) and key.value == "user_id"
                                for key in value.keys
                                if key is not None
                            )
                        ):
                            return True
            return False

    _StatementVisitor().visit(tree)
    return offenders


def test_exchange_keys_and_exchange_connections_access_applies_user_id_predicate() -> None:
    """Every ``.table("exchange_keys")`` / ``.table("exchange_connections")`` statement across
    the three files the audit's entries for these tables cite carries ``.eq("user_id", ...)`` on
    the same statement.

    The audit records that ``exchange_keys``/``exchange_connections`` carry NO foreign key on
    ``user_id`` at the schema layer (defects 1.50, 1.51). With no FK and no local PostgreSQL to
    exercise RLS against, the application predicate checked here is the only enforcement this
    test suite can verify runs on every access path.
    """
    table_names = frozenset({"exchange_keys", "exchange_connections"})
    files = (EXCHANGE_ROUTER_PATH, API_KEY_VAULT_PATH, DASHBOARD_AGGREGATION_PATH)

    offenders: List[str] = []
    touched_any = False
    for path in files:
        assert path.exists(), f"expected file cited by the audit is missing: {path}"
        tree = _parse(path)
        if any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "table"
            and set(_string_constants_in_call(node)) & table_names
            for node in ast.walk(tree)
        ):
            touched_any = True
        offenders.extend(_table_call_chains_missing_user_id(path, table_names))

    assert touched_any, (
        "none of the three files the audit cites for exchange_keys/exchange_connections contain "
        "a .table(...) call against either table any more; the audit's application-enforcement "
        "claim for these tables needs to be re-verified against wherever the access moved."
    )
    assert not offenders, (
        "the following exchange_keys/exchange_connections statements carry no .eq('user_id', "
        "...) predicate: " + "; ".join(offenders)
    )


# ---------------------------------------------------------------------------
# 3. The audit's own gap claims, pinned against the migration text they cite
# ---------------------------------------------------------------------------


def _strip_sql_comments(sql: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


def test_known_gap_tables_are_accurately_described_by_the_audit() -> None:
    """The three schema-level gaps the audit files (1.48 paper_* user_id FK, 1.50 exchange_keys
    FK, 1.51 exchange_connections FK) are checked directly against the migration text, so the
    audit cannot silently go stale if a later change closes one of them without updating
    ``tenant-boundary-audit.md`` to match.

    This test currently asserts the GAP IS PRESENT (an absent FK), matching the audit's current
    findings. If a future migration adds the missing foreign key, this test will start failing -
    which is the correct outcome: it means the audit document must be updated to move that
    table from "GAP" to "OK" rather than silently going out of date.
    """
    assert AUDIT_PATH.exists(), (
        "tenant-boundary-audit.md is missing; task 12.5's deliverable was not checked in."
    )
    audit_text = AUDIT_PATH.read_text(encoding="utf-8")
    for marker in ("P0-1", "P0-2", "P0-3", "P0-4", "P0-5", "1.48", "1.49", "1.50", "1.51", "1.52"):
        assert marker in audit_text, (
            f"tenant-boundary-audit.md no longer mentions {marker}; if the defect was "
            "renumbered, update this test's markers to match."
        )

    # --- 1.48 / P0-2: no paper_* CHILD table's user_id carries REFERENCES auth.users(id).
    # paper_sessions.user_id is the one deliberate exception - it is the root of the cascade and
    # is EXPECTED to carry the FK (section 3's table DDL); every other paper_* table denormalises
    # user_id onto the row with no FK at all, which is the gap. So the check is per-table, not a
    # single count over the whole file: paper_sessions must have exactly one FK'd user_id, and
    # every other paper_* table's user_id declaration must have none.
    paper_sql = _strip_sql_comments(PAPER_TRADING_MIGRATION.read_text(encoding="utf-8"))
    create_table_blocks = re.findall(
        r"CREATE TABLE IF NOT EXISTS public\.(paper_\w+)\s*\((.*?)\n\);",
        paper_sql,
        re.DOTALL,
    )
    assert len(create_table_blocks) >= 11, (
        f"expected at least 11 'CREATE TABLE IF NOT EXISTS public.paper_*' blocks in "
        f"{PAPER_TRADING_MIGRATION.name}, found {len(create_table_blocks)}. The block-extraction "
        "regex may need updating to match a reformatted file."
    )

    unreferenced_child_tables: List[str] = []
    referenced_child_tables: List[str] = []
    for table_name, body in create_table_blocks:
        has_user_id_fk = bool(
            re.search(r"\buser_id\s+UUID\s+NOT\s+NULL\s+REFERENCES", body)
        )
        has_bare_user_id = bool(
            re.search(r"\buser_id\s+UUID\s+NOT\s+NULL\s*,", body)
        )
        if table_name == "paper_sessions":
            assert has_user_id_fk, (
                "paper_sessions.user_id is expected to carry REFERENCES auth.users(id) - it is "
                "the root of the cascade every other paper_* table hangs from. If this FK was "
                "removed, that is a separate, more severe defect than 1.48/1.49 and must be "
                "filed as its own P0, not folded into this assertion."
            )
        elif has_user_id_fk:
            referenced_child_tables.append(table_name)
        elif has_bare_user_id:
            unreferenced_child_tables.append(table_name)

    assert not referenced_child_tables, (
        f"the following paper_* child table(s) now carry a foreign key on user_id: "
        f"{sorted(referenced_child_tables)}. Defect 1.48/1.49 (P0-2) may be closed for these "
        "tables - update tenant-boundary-audit.md's verdict from GAP to OK for each one before "
        "changing this assertion."
    )
    assert len(unreferenced_child_tables) >= 9, (
        f"expected at least 9 paper_* child tables with an unreferenced 'user_id UUID NOT "
        f"NULL' column (every owned table except paper_sessions itself), found "
        f"{len(unreferenced_child_tables)}: {sorted(unreferenced_child_tables)}. If this number "
        "fell because a table gained a foreign key, update the audit's verdict for that table "
        "before changing this assertion."
    )

    # --- 1.50: exchange_keys.user_id carries no FK ---
    exchange_keys_sql = _strip_sql_comments(
        EXCHANGE_KEYS_MIGRATION.read_text(encoding="utf-8")
    )
    assert re.search(r"\buser_id\s+TEXT\s+NOT\s+NULL\s*,", exchange_keys_sql), (
        f"expected 'user_id TEXT NOT NULL,' with no REFERENCES clause in "
        f"{EXCHANGE_KEYS_MIGRATION.name}. If a foreign key was added, defect 1.50 is closed: "
        "update tenant-boundary-audit.md before changing this assertion."
    )
    assert "REFERENCES" not in exchange_keys_sql.split("CREATE TABLE")[1].split(");")[0], (
        f"a REFERENCES clause appeared inside the exchange_keys CREATE TABLE block in "
        f"{EXCHANGE_KEYS_MIGRATION.name}; defect 1.50 may be closed - update the audit."
    )

    # --- 1.51: exchange_connections.user_id carries no FK ---
    exchange_connections_sql = _strip_sql_comments(
        EXCHANGE_CONNECTIONS_MIGRATION.read_text(encoding="utf-8")
    )
    assert re.search(r"\buser_id\s+TEXT\s+NOT\s+NULL\s*,", exchange_connections_sql), (
        f"expected 'user_id TEXT NOT NULL,' with no REFERENCES clause in "
        f"{EXCHANGE_CONNECTIONS_MIGRATION.name}. If a foreign key was added, defect 1.51 is "
        "closed: update tenant-boundary-audit.md before changing this assertion."
    )
    assert (
        "REFERENCES"
        not in exchange_connections_sql.split("CREATE TABLE")[1].split(");")[0]
    ), (
        f"a REFERENCES clause appeared inside the exchange_connections CREATE TABLE block in "
        f"{EXCHANGE_CONNECTIONS_MIGRATION.name}; defect 1.51 may be closed - update the audit."
    )


if __name__ == "__main__":  # pragma: no cover
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
