"""The three assertions that keep the public Listing projection the only public read.

Feature: marketplace-subscriptions-paper-trading, task 8.3.
Design reference: ``design.md`` -> "One implementation, shared by every path. ... Three
assertions keep it that way".
Requirements 6.1, 6.2, 6.4.

Tests living here
-----------------
``test_no_star_select_on_listings``       AST walk of ``library.py``: no ``select("*")`` on a
                                         ``table("library_strategies")`` chain
``test_every_public_read_projects``       AST walk: every non-ownership ``@router.get`` that
                                         reads Listing rows calls ``project_listing`` and
                                         returns nothing bound from an ``.execute()`` result
``test_projection_output_is_allow_listed``  Hypothesis ``listing_rows()`` (every denied column
                                         populated) through ``project_listing``, asserting
                                         ``set(result) <= PUBLIC_LISTING_FIELDS``

TWO OF THESE THREE ARE RED ON PURPOSE UNTIL TASK 16 LANDS
---------------------------------------------------------
``test_no_star_select_on_listings`` and ``test_every_public_read_projects`` are written
against the *current* ``backend_app/routers/library.py``, which serves
``GET /api/library/{id}`` as ``select("*")`` plus ``dict(resp.data)`` plus
``pop("author_id")`` - the shape Requirement 6.1 forbids and task 16 replaces. They fail now,
and that failure is the finding: it is the mechanical record of the defect the repository
audit classified EXISTS+BROKEN. They must not be weakened to pass. Task 16.1 and 16.2 turn
them green by repointing the reads at ``listing_projection.LISTING_SELECT`` and
``project_listing``; after that they are the regression guard that stops the ``select("*")``
shape from coming back.

The offenders each assertion names today, and what clears them:

* ``get_library_detail``'s ``select("*")`` (``library.py`` ~line 949) - task 16.1.
* ``create_marketplace_checkout``'s ``select("*")`` (~line 1825) - task 18.2, which rewrites
  that read. The star check is file-wide because ``design.md`` states it file-wide ("no
  ``.select("*")`` argument on a ``.table("library_strategies")`` chain"), and a full-row read
  in the checkout path is exactly as easy to hand to a response tomorrow as one in a
  catalogue path. So this test needs both task 16 and task 18.2 to go green.
* ``browse_library``, ``get_featured_strategies``, ``get_trending_strategies``,
  ``get_library_detail``, ``get_creator_profile``, ``get_recommendations`` and
  ``get_user_favorites`` return rows the database handed them, unprojected - tasks 16.1, 16.2.

WHY THIS IS AN AST WALK AND NOT AN HTTP TEST
--------------------------------------------
An HTTP test can only observe the columns a *particular* row happened to carry. The defect
Requirement 6.1 addresses is structural: ``select("*")`` exposes whatever the next migration
adds, on the day it is added, with no code change and therefore no test change. Only a check
on the query shape can see that. The two walks are static, need no database, no fixture and
no event loop, and run in milliseconds - which is what lets them sit in the default
``pytest tests/`` lane as a permanent guard.

HOW "READS LISTING ROWS" AND "OWNERSHIP CHECK" ARE DECIDED, AND WHY MECHANICALLY
-------------------------------------------------------------------------------
``design.md`` says "every function decorated with a ``@router.get`` whose dependencies do not
include an ownership check must contain a call to ``project_listing``". Read with no further
qualification that sweeps in ``get_categories`` (which returns per-category *counts*),
``get_subscription_status``, ``check_deployment_permission_endpoint``, ``get_strategy_reviews``
and ``subscriber_analytics`` - none of which returns a Listing row, none of which will ever
call ``project_listing``, and all of which would make this test permanently and pointlessly
red. So the scope is decided from the query, not from a hand-maintained exemption list that
would silently stop covering a handler somebody renames:

*Reads Listing rows* - the handler has a ``table("library_strategies")`` chain whose
``select`` argument is ``"*"``, is absent, names at least
:data:`LISTING_ROW_COLUMN_FLOOR` columns, or is not a literal at all (which is the shape
``select(listing_projection.LISTING_SELECT)`` has after task 16, and must count). A
one-column ``select("category")`` aggregate read is not a Listing row read; a
two-column ``select("id, source_library_id")`` clone-enrichment lookup is not either.

*Ownership check* - the handler is admin-gated (a ``Depends`` on a dependency whose name
contains ``admin``), or every Listing-row chain in it is filtered by
``eq("author_id", <the authenticated caller>)``. The comparand has to trace back to a
``Depends``-injected identity: ``my_library``'s ``.eq("author_id", user_id)`` where
``user_id`` came from ``user["id"]`` is an ownership filter, and
``get_creator_profile``'s ``.eq("author_id", creator_uid)`` where ``creator_uid`` came from
the *path* is not - that endpoint serves one creator's catalogue to the whole internet and
must project.

The design's own list of public GET reads is asserted on directly as well, so that a future
refactor which moves a query behind a helper (and so drops the handler out of the mechanical
scope above) cannot quietly reduce this test to a tautology.

WHY THE RETURN CHECK TRACKS TAINT RATHER THAN MATCHING ``return resp.data``
--------------------------------------------------------------------------
``return resp.data`` is not the shape the defect takes. ``get_library_detail`` does
``detail = dict(resp.data)`` and thirty lines later ``return detail``;
``get_featured_strategies`` does ``items = resp.data or []``, then
``response_data = {"items": items}``, then ``return response_data``. A pattern match on the
``return`` statement sees neither. So a name is *tainted* when it is bound from an expression
containing ``.execute()`` or from another tainted name, transitively - and untainted when the
binding expression passes through ``project_listing``, because that is precisely the
laundering the requirement asks for. A tainted name anywhere in a ``return`` expression is
the violation, except where it appears as an argument *to* ``project_listing``: after task 16
``return project_listing(row, evidence, alias)`` returns a projection of a tainted row, which
is the fix, not the defect.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from hypothesis import HealthCheck, given, settings

from backend_app.backend.marketplace.listing_projection import (
    PUBLIC_LISTING_FIELDS,
    project_listing,
)
from tests.strategies.marketplace_generators import (
    assert_denied_columns_in_sync,
    denied_listing_columns,
    listing_rows,
    tokens,
)

# ══════════════════════════════════════════════════════════════════════════
# What is walked, and the constants that decide the scope
# ══════════════════════════════════════════════════════════════════════════

REPO_ROOT: Path = Path(__file__).resolve().parents[1]

#: The single module every Marketplace HTTP read lives in today.
LIBRARY_ROUTER: Path = REPO_ROOT / "backend_app" / "routers" / "library.py"

#: The one authoritative Listing table (Requirement 1.1).
LISTING_TABLE: str = "library_strategies"

#: A ``select`` naming at least this many columns is fetching a Listing *row*, not a single
#: aggregate column. Three is the smallest count that excludes every aggregate and
#: enrichment read in ``library.py`` today (``select("category")``,
#: ``select("id, source_library_id")``, ``select("id, source_strategy_id")``) while
#: including every catalogue and detail read.
LISTING_ROW_COLUMN_FLOOR: int = 3

#: The one sanctioned public serialiser (task 8.1).
PROJECTION_CALL: str = "project_listing"

#: Parameter names FastAPI injects the authenticated identity into in this router.
CALLER_PARAMETERS: frozenset = frozenset({"user", "admin", "current_user"})

#: Calls that reduce rows to a scalar. A name bound from one of these holds a count, a total
#: or an average - a figure *about* the rows, not the rows - so it is not row content and
#: returning it discloses no column. ``dict``, ``list`` and ``str`` are deliberately absent:
#: each of those keeps the row, and ``dict(resp.data)`` is the exact shape task 16.1 removes.
ROW_REDUCING_CALLS: frozenset = frozenset(
    {"len", "sum", "min", "max", "any", "all", "round", "count"}
)

#: ``design.md`` -> "One implementation, shared by every path", restricted to the ``@router.get``
#: handlers of that list. ``compare_strategies`` is a ``@router.post`` and ``my_library`` is
#: owner-scoped, so neither is required here by name; both are still covered by the mechanical
#: scope if their queries stop being owner-filtered.
DESIGN_PUBLIC_GET_READS: frozenset = frozenset(
    {
        "browse_library",
        "get_featured_strategies",
        "get_trending_strategies",
        "get_library_detail",
        "get_creator_profile",
        "get_recommendations",
        "get_user_favorites",
    }
)

#: The configuration ``design.md § Property-based testing configuration`` prescribes for every
#: property test in this plan: at least 100 examples and no per-example deadline.
PROPERTY_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

FunctionNode = Any  # ast.FunctionDef | ast.AsyncFunctionDef


# ══════════════════════════════════════════════════════════════════════════
# Generic AST helpers
# ══════════════════════════════════════════════════════════════════════════


def _router_tree() -> ast.Module:
    """Parse ``library.py``. Read from disk, so no import side effect is triggered."""
    source = LIBRARY_ROUTER.read_text(encoding="utf-8")
    return ast.parse(source, filename=str(LIBRARY_ROUTER))


def _callee_name(call: ast.Call) -> Optional[str]:
    """``f(...)`` -> ``"f"``; ``a.b.f(...)`` -> ``"f"``; anything else -> ``None``."""
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _receiver_chain(call: ast.Call) -> List[ast.Call]:
    """Every call in ``call``'s receiver chain, outermost first.

    ``svc.table("t").select("c").eq("a", b).execute()`` walked from the ``execute()`` node
    yields ``[execute, eq, select, table]`` - which is what makes "does this chain touch
    ``library_strategies``" answerable from any link in it.
    """
    chain: List[ast.Call] = []
    node: Optional[ast.Call] = call
    while isinstance(node, ast.Call):
        chain.append(node)
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Call):
            node = func.value
        else:
            node = None
    return chain


def _first_string_argument(call: ast.Call) -> Optional[str]:
    """The call's first positional argument if it is a string literal."""
    if call.args and isinstance(call.args[0], ast.Constant):
        value = call.args[0].value
        if isinstance(value, str):
            return value
    return None


def _chain_table(call: ast.Call) -> Optional[str]:
    """The table name the chain containing ``call`` reads, or ``None``."""
    for link in _receiver_chain(call):
        if _callee_name(link) == "table":
            return _first_string_argument(link)
    return None


def _mentions(node: ast.AST, names: Set[str]) -> bool:
    """True when ``node`` reads any name in ``names``."""
    return any(
        isinstance(child, ast.Name) and child.id in names for child in ast.walk(node)
    )


def _contains_call(node: ast.AST, callee: str) -> bool:
    """True when ``node`` contains a call to ``callee``, however it is qualified."""
    return any(
        isinstance(child, ast.Call) and _callee_name(child) == callee
        for child in ast.walk(node)
    )


def _bound_names(target: ast.AST) -> List[str]:
    """Every plain name an assignment target binds, tuple targets included."""
    return [child.id for child in ast.walk(target) if isinstance(child, ast.Name)]


def _enclosing_functions(tree: ast.Module) -> Dict[int, str]:
    """Map ``id(node)`` to the name of the innermost function containing it."""
    owners: Dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.walk(node):
                owners[id(child)] = node.name
    return owners


# ══════════════════════════════════════════════════════════════════════════
# Listing-query helpers
# ══════════════════════════════════════════════════════════════════════════


def _listing_select_calls(node: ast.AST) -> List[ast.Call]:
    """Every ``select(...)`` call issued against ``library_strategies`` under ``node``."""
    return [
        child
        for child in ast.walk(node)
        if isinstance(child, ast.Call)
        and _callee_name(child) == "select"
        and _chain_table(child) == LISTING_TABLE
    ]


def _selects_every_column(call: ast.Call) -> bool:
    """True when the ``select`` asks for the whole row.

    ``select("*")`` and a bare ``select()`` are the same request: supabase-py defaults to
    every column. ``"*"`` is looked for per comma-separated token, so
    ``select("*, library_ratings(rating)")`` is caught too.
    """
    if not call.args:
        return True
    for arg in call.args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            if any(part.strip() == "*" for part in arg.value.split(",")):
                return True
    return False


def _is_listing_row_read(call: ast.Call) -> bool:
    """True when this ``select`` pulls Listing row content, not one aggregate column."""
    if _selects_every_column(call):
        return True
    for arg in call.args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            columns = [part.strip() for part in arg.value.split(",") if part.strip()]
            if len(columns) >= LISTING_ROW_COLUMN_FLOOR:
                return True
        else:
            # A non-literal column list - ``select(listing_projection.LISTING_SELECT)``,
            # which is the post-task-16 shape and is unambiguously a row read.
            return True
    return False


def _listing_chains(node: ast.AST) -> List[ast.Call]:
    """The maximal (outermost) call of every ``library_strategies`` chain under ``node``.

    Anchoring on the outermost call is what makes the ``.eq("author_id", …)`` filter
    visible: it sits *outside* the ``select`` in the chain, so a walk that started at the
    ``select`` node could never see it.
    """
    inner: Set[int] = set()
    for child in ast.walk(node):
        if (
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and isinstance(child.func.value, ast.Call)
        ):
            inner.add(id(child.func.value))
    return [
        child
        for child in ast.walk(node)
        if isinstance(child, ast.Call)
        and id(child) not in inner
        and _chain_table(child) == LISTING_TABLE
    ]


# ══════════════════════════════════════════════════════════════════════════
# Handler helpers
# ══════════════════════════════════════════════════════════════════════════


def _router_names(tree: ast.Module) -> Set[str]:
    """Every module-level name bound to an ``APIRouter()``.

    Discovered rather than hard-coded to ``"router"``: this module carries a second router
    (``literal_router``, so that literal paths register ahead of ``/{library_id}``), and a
    detector that only knew the one name would have silently stopped seeing five of the
    catalogue handlers the moment that split landed.
    """
    names: Set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        if _callee_name(node.value) != "APIRouter":
            continue
        for target in node.targets:
            names.update(_bound_names(target))
    return names


def _get_handlers(tree: ast.Module) -> List[FunctionNode]:
    """Every function decorated with ``@<router>.get(...)``."""
    routers = _router_names(tree)
    handlers: List[FunctionNode] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "get"
                and isinstance(decorator.func.value, ast.Name)
                and decorator.func.value.id in routers
            ):
                handlers.append(node)
                break
    return handlers


def _parameter_defaults(function: FunctionNode) -> List[Tuple[ast.arg, ast.AST]]:
    """``(parameter, default)`` for every parameter that has a default."""
    spec = function.args
    positional = list(spec.posonlyargs) + list(spec.args)
    pairs: List[Tuple[ast.arg, ast.AST]] = []
    if spec.defaults:
        for parameter, default in zip(positional[-len(spec.defaults) :], spec.defaults):
            pairs.append((parameter, default))
    for parameter, default in zip(spec.kwonlyargs, spec.kw_defaults):
        if default is not None:
            pairs.append((parameter, default))
    return pairs


def _dependency_names(function: FunctionNode) -> List[str]:
    """The dependency callables the handler declares through ``Depends(...)``."""
    names: List[str] = []
    for _parameter, default in _parameter_defaults(function):
        if (
            isinstance(default, ast.Call)
            and _callee_name(default) == "Depends"
            and default.args
        ):
            names.append(ast.unparse(default.args[0]))
    return names


def _is_admin_gated(function: FunctionNode) -> bool:
    """True when a declared dependency is an administrative one.

    ``get_admin_user`` resolves ``app_metadata.role`` in ``admin``/``support``/``operator``,
    so a handler behind it is a review surface rather than a public catalogue read.
    """
    return any("admin" in name for name in _dependency_names(function))


def _caller_identity_names(function: FunctionNode) -> Set[str]:
    """Every name in the handler that holds the authenticated caller's identity.

    Seeded with the ``Depends``-injected identity parameters, then closed over assignment:
    ``user_id = _safe_uuid(user["id"], "user_id")`` makes ``user_id`` a caller identity, and
    a path parameter never becomes one.
    """
    names: Set[str] = set()
    for parameter, default in _parameter_defaults(function):
        if (
            isinstance(default, ast.Call)
            and _callee_name(default) == "Depends"
            and parameter.arg in CALLER_PARAMETERS
        ):
            names.add(parameter.arg)

    changed = bool(names)
    while changed:
        changed = False
        for node in ast.walk(function):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            if value is None or not _mentions(value, names):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                for name in _bound_names(target):
                    if name not in names:
                        names.add(name)
                        changed = True
    return names


def _is_owner_filtered(chain: ast.Call, caller_names: Set[str]) -> bool:
    """True when the chain constrains ``author_id`` to the authenticated caller."""
    for link in _receiver_chain(chain):
        if _callee_name(link) != "eq" or len(link.args) < 2:
            continue
        column = link.args[0]
        if not (isinstance(column, ast.Constant) and column.value == "author_id"):
            continue
        if _mentions(link.args[1], caller_names):
            return True
    return False


def _reads_listing_rows_for_non_owner(function: FunctionNode) -> bool:
    """True when the handler reads Listing rows it does not scope to the caller."""
    if _is_admin_gated(function):
        return False
    caller_names = _caller_identity_names(function)
    for chain in _listing_chains(function):
        selects = [
            link for link in _receiver_chain(chain) if _callee_name(link) == "select"
        ]
        if not any(_is_listing_row_read(link) for link in selects):
            continue
        if not _is_owner_filtered(chain, caller_names):
            return True
    # Every Listing-row chain was owner-filtered, or the handler reads no Listing row.
    return False


def _carries_rows(value: ast.AST, tainted: Set[str]) -> bool:
    """True when ``value`` evaluates to database row content that was never projected."""
    if _contains_call(value, PROJECTION_CALL):
        # Laundered through the allow-list; that is the sanctioned path.
        return False
    if isinstance(value, ast.Call) and _callee_name(value) in ROW_REDUCING_CALLS:
        # A count, a total or an average of the rows, not the rows.
        return False
    return _contains_call(value, "execute") or _mentions(value, tainted)


def _tainted_names(function: FunctionNode) -> Set[str]:
    """Every name holding, transitively, unprojected database row content.

    Four bindings propagate row content, and ``library.py`` uses all four today:
    assignment (``detail = dict(resp.data)``), iteration (``for item in paginated``),
    accumulation (``items.append(card)`` - which is how ``browse_library`` builds its page,
    starting from an empty list literal that no assignment-only tracker would ever taint)
    and augmented assignment (``items += rows``).
    """
    tainted: Set[str] = set()
    changed = True
    while changed:
        changed = False
        for node in ast.walk(function):
            targets: List[ast.AST] = []
            value: Optional[ast.AST] = None

            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = node.value
                targets = (
                    list(node.targets)
                    if isinstance(node, ast.Assign)
                    else [node.target]
                )
            elif isinstance(node, ast.AugAssign):
                value = node.value
                targets = [node.target]
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                value = node.iter
                targets = [node.target]
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"append", "extend", "insert", "update"}
                and isinstance(node.func.value, ast.Name)
                and node.args
            ):
                # ``items.append(card)`` makes ``items`` hold whatever ``card`` holds.
                value = node.args[-1]
                targets = [node.func.value]

            if value is None or not targets:
                continue
            if not _carries_rows(value, tainted):
                continue
            for target in targets:
                for name in _bound_names(target):
                    if name not in tainted:
                        tainted.add(name)
                        changed = True
    return tainted


def _projected_name_nodes(function: FunctionNode) -> Set[int]:
    """``id()`` of every name node that is an argument to ``project_listing``."""
    projected: Set[int] = set()
    for node in ast.walk(function):
        if isinstance(node, ast.Call) and _callee_name(node) == PROJECTION_CALL:
            arguments = list(node.args) + [keyword.value for keyword in node.keywords]
            for argument in arguments:
                for child in ast.walk(argument):
                    if isinstance(child, ast.Name):
                        projected.add(id(child))
    return projected


def _unprojected_returns(function: FunctionNode) -> List[Tuple[int, str]]:
    """``(line, name)`` for every ``return`` handing back an unprojected result."""
    tainted = _tainted_names(function)
    if not tainted:
        return []
    projected = _projected_name_nodes(function)
    offenders: Set[Tuple[int, str]] = set()
    for node in ast.walk(function):
        if not isinstance(node, ast.Return) or node.value is None:
            continue
        for child in ast.walk(node.value):
            if (
                isinstance(child, ast.Name)
                and child.id in tainted
                and id(child) not in projected
            ):
                offenders.add((node.lineno, child.id))
    return sorted(offenders)


# ══════════════════════════════════════════════════════════════════════════
# The assertions
# ══════════════════════════════════════════════════════════════════════════


def test_no_star_select_on_listings() -> None:
    """No ``library_strategies`` read asks the database for the whole row.

    ``select("*")`` is default-open: the column a future migration adds is fetched the moment
    it exists, and the only thing between it and a response is somebody remembering to
    ``pop`` it. The explicit ``listing_projection.LISTING_SELECT`` column list inverts that
    default, which is what Requirement 6.1's "SHALL NOT return a Listing by serialising the
    whole database row" needs at the query layer.

    _Requirements: 6.1, 6.4_
    """
    tree = _router_tree()
    owners = _enclosing_functions(tree)

    offenders = [
        (
            call.lineno,
            owners.get(id(call), "<module>"),
            ast.unparse(call)[:120],
        )
        for call in _listing_select_calls(tree)
        if _selects_every_column(call)
    ]

    assert not offenders, (
        f"{len(offenders)} whole-row read(s) of {LISTING_TABLE!r} in "
        f"{LIBRARY_ROUTER.name}; each must select "
        "listing_projection.LISTING_SELECT explicitly:\n"
        + "\n".join(
            f"  line {line} in {function}: {text}" for line, function, text in offenders
        )
    )


def test_every_public_read_projects() -> None:
    """Every public Listing read goes through ``project_listing`` and returns its output.

    Two obligations, reported together because they are two halves of one property: a
    handler that reads Listing rows for a non-owner must (a) call the one sanctioned
    serialiser and (b) not hand back a value that came from the driver instead.

    _Requirements: 6.1, 6.2, 6.4_
    """
    tree = _router_tree()
    handlers = {node.name: node for node in _get_handlers(tree)}

    assert handlers, (
        f"no @router.get handler found in {LIBRARY_ROUTER.name}; the decorator detector "
        "has stopped matching and every assertion below would be vacuous"
    )

    # The design names these by hand, so a refactor that hides a query behind a helper
    # cannot drop them out of the mechanical scope unnoticed.
    missing_by_name = sorted(DESIGN_PUBLIC_GET_READS - set(handlers))
    assert not missing_by_name, (
        "design.md names these as public GET Listing reads but they are not "
        f"@router.get handlers in {LIBRARY_ROUTER.name}: {missing_by_name}. If a handler "
        "was renamed, update DESIGN_PUBLIC_GET_READS in the same change."
    )

    public_reads = sorted(
        set(DESIGN_PUBLIC_GET_READS)
        | {
            name
            for name, node in handlers.items()
            if _reads_listing_rows_for_non_owner(node)
        }
    )

    problems: List[str] = []
    for name in public_reads:
        node = handlers[name]
        if not _contains_call(node, PROJECTION_CALL):
            problems.append(
                f"  {name} (line {node.lineno}) never calls {PROJECTION_CALL}()"
            )
        for line, returned in _unprojected_returns(node):
            problems.append(
                f"  {name} returns {returned!r} at line {line}, which is bound from an "
                ".execute() result and was never passed through "
                f"{PROJECTION_CALL}()"
            )

    assert not problems, (
        f"{len(problems)} public Listing read(s) in {LIBRARY_ROUTER.name} bypass the "
        f"allow-list projection (examined: {public_reads}):\n" + "\n".join(problems)
    )


@PROPERTY_SETTINGS
@given(row=listing_rows(), creator_alias=tokens(min_size=3, max_size=24))
def test_projection_output_is_allow_listed(
    row: Dict[str, Any], creator_alias: str
) -> None:
    """A row carrying every denied column still projects to allow-listed keys only.

    ``listing_rows()`` populates every member of ``DENIED_LISTING_COLUMNS`` with a non-null
    value, so the row under test holds ``author_id``, ``source_strategy_id``,
    ``moderation_notes``, ``moderated_by``, ``evaluation_score``,
    ``deployment_requirements``, ``version_history`` and the rest - everything the projection
    has to refuse. The output being a *subset* rather than an equality is deliberate:
    Requirement 6.9's omit-rather-than-zero rating branch and an unread evidence set both
    produce a strict subset, and the allow-list is an upper bound on the key set.

    **Validates: Requirements 6.1, 6.2, 6.4**
    """
    # The generators fall back to a transcribed copy of the denied set when task 8.1's
    # module is absent. It is present now, so hold the two definitions to each other -
    # otherwise this test could pass against a stale idea of what "denied" means.
    assert_denied_columns_in_sync()

    result = project_listing(row, None, creator_alias)

    surplus = sorted(set(result) - PUBLIC_LISTING_FIELDS)
    assert not surplus, (
        f"project_listing emitted {surplus}, which PUBLIC_LISTING_FIELDS does not name; "
        f"the row carried {len(row)} columns"
    )

    leaked = sorted(set(result) & denied_listing_columns())
    assert not leaked, (
        f"project_listing emitted denied column(s) {leaked}; every one of them was "
        "populated with a non-null value in the row under test"
    )
