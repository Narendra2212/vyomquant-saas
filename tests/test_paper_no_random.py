"""
tests/test_paper_no_random.py - the second of task 25.1's two simulator guards.

Spec: marketplace-subscriptions-paper-trading task 25.1. ``design.md`` ->
"``paper/paper_simulator.py``". Requirements 13.8, 13.11, 14.9, 15.4, 16.12, 18.1, 28.3.

WHAT THIS MODULE IS
-------------------
An AST walk over **every** module under ``backend_app/backend/paper/``, failing on:

* ``import random`` (and ``import random.foo``, and ``import random as r``)
* ``from random import ...`` (and ``from random.foo import ...``)
* any ``numpy.random`` attribute access, however the module was imported or aliased
* any import of ``backend_app.backend.exchange_simulator``

The first guard - ``paper_simulator.assert_paper_simulator`` - is a runtime refusal and is tested
in ``tests/test_paper_simulator_config.py``. This one is static, and it has to be: "no module in
this package can reach a randomised value" is a property of the package's source, and no runtime
branch can observe it. A module that imported ``random`` and never called it would pass every
behavioural test ever written and would still be one line away from a fabricated price.

WHY AN AST WALK AND NOT A ``grep``
---------------------------------
A text search matches the word ``random`` in a docstring - this file's own subject matter is full
of it, and so is ``paper_simulator``'s module docstring, which quotes the four offending lines of
``exchange_simulator.py`` verbatim. Parsing means a *comment about* randomness reads as prose and
an *import of* randomness reads as an import, which is the distinction the guard is about.

THIS MODULE IS THE AUTHORITATIVE CHECK, AND ITS RELATIONSHIP TO THE OLDER ONE
----------------------------------------------------------------------------
``tests/test_paper_market_feed_selection.py::TestNoSecondMarketDataPath::
test_no_module_in_the_paper_package_imports_random`` (task 24.x) is the narrower predecessor. It
walks the same package for the same two ``import random`` forms and nothing else.

:func:`test_no_paper_module_imports_random` **subsumes it entirely**: same package, same two
forms, plus dotted and aliased spellings, plus ``numpy.random``, plus the
``exchange_simulator`` import. The older assertion is left where it is rather than deleted, for
one stated reason: it is a member of the market-feed suite, and that suite is run on its own by
the feed's own verification command. A feed change that reached for a random jitter should turn
the feed suite red without needing the whole paper suite - a check that only fires in a different
file is a check a developer running one file does not have.

So the relationship is deliberate and one-directional: **this module is authoritative and
strictly broader; the feed suite keeps a local canary that is a proper subset of it.** They
cannot disagree - a package state that fails there fails here too - and :func:`
test_the_narrower_predecessor_is_a_proper_subset_of_this_module` asserts exactly that
containment, so the two cannot drift apart silently either. If the feed check is ever widened
beyond this module's scope, that test fails and says so.

WHY ``numpy.random`` IS CHECKED SEPARATELY
------------------------------------------
``numpy`` is already a dependency of this repository (the backtesting engine uses it), so
``import numpy as np`` is unremarkable and cannot be banned. ``np.random.normal`` is exactly as
much a fabricated price as ``random.gauss`` is, and it reaches the same place through a name the
``random`` check never sees. So the walk looks for the **attribute chain** ``.random`` on
anything named ``numpy`` or aliased from it, rather than for an import.

WHAT IS NOT CLAIMED
-------------------
A static walk cannot see a randomness source reached through ``importlib.import_module`` or
``__import__`` with a computed name, or through a third module that itself draws. So
:func:`test_no_paper_module_imports_a_module_by_computed_name` closes the first hole by refusing
dynamic imports in this package outright, and the transitive case is bounded by what the package
imports at all: :func:`test_every_first_party_import_of_the_paper_package_is_enumerated` pins the
first-party module list, so a new dependency has to be added to this test - and justified - before
it can be added to the package.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Dict, List, Set, Tuple

import pytest

from backend_app.backend.paper import paper_simulator as sim

# ══════════════════════════════════════════════════════════════════════════
#  THE PACKAGE, AND ITS PARSED SOURCE
# ══════════════════════════════════════════════════════════════════════════

#: The directory under audit. Resolved from the module object rather than written as a path, so a
#: package move cannot leave this walking an empty directory and passing.
PAPER_PACKAGE = Path(inspect.getfile(sim)).parent

#: The repository root, resolved from this file so it does not depend on the working directory.
REPO_ROOT = Path(__file__).resolve().parents[1]

#: The module whose presence in the paper package would defeat the whole of task 25.1.
FORBIDDEN_MODULE = "backend_app.backend.exchange_simulator"

#: The first-party modules the paper package is permitted to import. Each was read; the note
#: records what was found, including where the answer is not a flat "no ``random`` anywhere":
#:
#: * ``backend_app.backend.asset_universe``       market metadata. No ``random``. Its two heavy
#:   imports (``connection_engine``, ``exchange_executor``) are function-local, so importing it
#:   pulls stdlib only and puts no exchange client in the paper import graph (Requirement 13.2).
#: * ``backend_app.backend.market_data_latency``  the correctness floor. No ``random``.
#: * ``backend_app.backend.market_data_contract`` normalisation. No ``random``; it *names*
#:   ``SYNTHETIC_FILL`` in a deny-list, which is prose about randomness, not a draw.
#: * ``backend_app.backend.market_data_validation``  **does import ``random``.** Two uses, and
#:   the paper package reaches neither: ``GapHandler._synthetic_fill`` raises
#:   ``RuntimeError("_synthetic_fill is deprecated. Synthetic data is prohibited.")`` on its first
#:   line, before any draw, and ``_generate_sample_data`` is a sample-data helper. The paper
#:   package imports exactly three symbols from it, pinned by
#:   :func:`test_the_paper_package_imports_only_the_validators_from_market_data_validation`.
#: * ``backend_app.backend.marketplace.errors`` / ``marketplace.money``  the shared error
#:   catalogue and the persisted minor-unit exponent table. No ``random`` in either.
#: * ``backend_app.backend.marketplace.entitlement_resolver``  (task 27.1) THE single admission
#:   decision. ``paper_session_service.start_session`` imports ``resolve``, ``Entitlement`` and
#:   ``EntitlementReadFailed`` from it at module scope, because Requirement 11.10 and property P-16
#:   make the deployment and Paper_Session start decisions the SAME decision - a second one in this
#:   package is the thing that must not exist, so the import is the point rather than a
#:   convenience. **Read: it imports only ``dataclasses``, ``datetime``, ``enum``,
#:   ``types.MappingProxyType`` and ``typing``** - no first-party module at all, not even
#:   ``marketplace.errors`` (its own docstring records that it references the wire-code catalogue
#:   strings BY NAME so as to stay free of FastAPI) - and it contains no occurrence of the word
#:   ``random``. It draws nothing: its whole output is one frozen ``Entitlement`` built from two
#:   reads and an identity comparison, and no price, quantity, fee or fill decision passes through
#:   it, so no draw of its could reach a figure Requirements 14.9, 16.12 or 28.3 are about even if
#:   one existed.
#: * ``backend_app.backend.metrics``              the collector, reached through its
#:   ``guarded_collector()`` accessor - the one guarded reader of the one collector, promoted there
#:   so the paper package does not re-spell the idiom five times (Requirement 30.2). Read: stdlib
#:   only at module scope (``functools``, ``logging``, ``re``, ``threading``, ``time``,
#:   ``collections``, ``dataclasses``, ``datetime``, ``typing``); its single first-party import,
#:   ``market_data_latency.LatencySummary``, is function-local and already on this list. No
#:   occurrence of the word ``random``, and nothing it does produces a price, quantity, fee or fill
#:   decision - it counts and summarises values handed to it.
#: * ``backend_app.core.cache``                   the Redis handle the Paper_Channel publishes
#:   through. No ``random`` (no reconnect jitter).
#: * ``backend_app.core.audit_trail``             the Audit_Log, imported at call time only.
#: * ``backend_app.backend.paper.*``              this package itself, walked by this module.
#: * ``backend_app.backend.order_lifecycle_state`` (task 26.1) the nine-value Order_Lifecycle_State
#:   enum ``paper_events.SignalGeneratedPayload`` validates against. **Read: it imports only
#:   ``enum`` and ``typing``** and contains no occurrence of the word ``random`` at all - it is a
#:   pure vocabulary leaf, which is exactly why ``paper_events`` imports it rather than re-spelling
#:   the nine values.
#: * ``backend_app.api_ws.ws_manager``           (task 26.4) the WebSocket connection registry and
#:   the per-connection pending-queue depth counter Requirement 19.11 is enforced against.
#:   ``paper_channel`` imports ``ConnectionManager``, the module singleton ``manager`` and
#:   ``MAX_PENDING_EVENTS_PER_CONNECTION`` from it at module scope. **Read: it imports
#:   ``asyncio``, ``json``, ``logging``, ``collections.defaultdict``, ``typing``, ``fastapi``
#:   (``HTTPException``, ``WebSocket``) and ``backend_app.core.cache`` (already on this list) - and
#:   contains no occurrence of the word ``random`` at all.** There is no reconnect jitter: its Redis
#:   listener loop sleeps a FIXED five seconds, and its only identifier-like value is a dictionary
#:   key that is the connection object itself. Nothing it does produces a price, a quantity, a fee
#:   or a fill decision - it moves already-built frames onto sockets - so no draw of its could reach
#:   a figure Requirements 14.9, 16.12 or 28.3 are about even if one existed.
#: * ``backend_app.backend.paper.paper_channel``  (task 26.4) this package's own delivery module,
#:   walked by this file like every other. ``paper_events.broadcast`` / ``paper_events.replay``
#:   reach it through FUNCTION-LOCAL imports, for the same circularity reason
#:   ``paper_events.paper_channel()`` reaches ``ws_channels`` that way.
#: * ``backend_app.backend.ws_channels``          (task 26.3) the channel-family registry.
#:   ``paper_events.paper_channel()`` reaches it through a FUNCTION-LOCAL import to build
#:   ``"paper.{session_id}"``, so the separator and the identifier shape are stated once, in
#:   ``PAPER_FAMILY``. **Read: it imports only ``re``, ``threading``, ``dataclasses``, ``datetime``,
#:   ``enum``, ``typing``, ``uuid`` and ``paper_events`` itself**, and contains no occurrence of the
#:   word ``random``. There is no reconnect jitter and no id draw on any path this call reaches -
#:   ``PAPER_FAMILY.channel()`` is a regex match and an f-string.
#:
#: A new entry here is a decision, which is the point: the transitive half of "no ``random``
#: under the paper package" cannot be settled by parsing this package alone, so the package's
#: dependency list is pinned instead and every addition has to be argued for in review.
PERMITTED_FIRST_PARTY_IMPORTS: Set[str] = {
    "backend_app.api_ws.ws_manager",
    "backend_app.backend.asset_universe",
    "backend_app.backend.market_data_contract",
    "backend_app.backend.market_data_latency",
    "backend_app.backend.market_data_validation",
    "backend_app.backend.marketplace.entitlement_resolver",
    "backend_app.backend.marketplace.errors",
    "backend_app.backend.marketplace.money",
    "backend_app.backend.metrics",
    "backend_app.backend.order_lifecycle_state",
    "backend_app.backend.paper",
    "backend_app.backend.paper.errors",
    "backend_app.backend.paper.paper_accounting",
    "backend_app.backend.paper.paper_channel",
    "backend_app.backend.paper.paper_events",
    "backend_app.backend.paper.paper_market_feed",
    "backend_app.backend.paper.paper_order_state",
    "backend_app.backend.paper.paper_repository",
    "backend_app.backend.paper.paper_session_service",
    "backend_app.backend.paper.paper_simulator",
    "backend_app.backend.ws_channels",
    "backend_app.core.audit_trail",
    "backend_app.core.cache",
}

#: The only symbols the paper package may import from the one permitted module that does import
#: ``random``. Pinned because "the paper path does not reach the random code" is a statement about
#: *which symbols* are imported, and only a test can keep it true.
PERMITTED_VALIDATION_SYMBOLS: Set[str] = {
    "CandleIntegrityValidator",
    "DataValidationError",
    "StructuralValidator",
}


def _paper_modules() -> List[Path]:
    """Every ``.py`` file in the package, ``__init__.py`` included, sorted for stable ids."""
    modules = sorted(PAPER_PACKAGE.glob("*.py"))
    assert modules, (
        f"no Python modules found under {PAPER_PACKAGE}; this walk would pass by finding "
        f"nothing, which is the one way a static guard fails silently"
    )
    return modules


def _tree(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


#: Parsed once per session - the walk runs several times over the same eight files.
_TREES: Dict[Path, ast.AST] = {}


def _parsed() -> Dict[Path, ast.AST]:
    if not _TREES:
        for path in _paper_modules():
            _TREES[path] = _tree(path)
    return dict(_TREES)


# ══════════════════════════════════════════════════════════════════════════
#  THE DETECTORS
# ══════════════════════════════════════════════════════════════════════════


def _imported_roots(tree: ast.AST) -> List[Tuple[str, int]]:
    """Every module root this file imports, as ``(root, lineno)``.

    ``import random`` -> ``random``; ``import random.foo as r`` -> ``random``;
    ``from random import gauss`` -> ``random``; ``from .paper_accounting import x`` -> ``""``
    (a relative import has no root name, and is reported by :func:`_imported_modules`).
    """
    roots: List[Tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.append((alias.name.split(".")[0], node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # a relative import: no absolute root to read
                roots.append(("", node.lineno))
            elif node.module:
                roots.append((node.module.split(".")[0], node.lineno))
    return roots


def _imported_modules(tree: ast.AST) -> List[Tuple[str, int]]:
    """Every module name this file imports, as ``(name, lineno)``.

    ``from X import y`` contributes ``X`` **and** ``X.y``, because ``y`` may be a submodule:
    ``from backend_app.backend import exchange_simulator`` is an import of the forbidden module
    and would otherwise read as an import of its package. The cost is that a plain *symbol*
    import also contributes a name that is not a module - harmless for the forbidden-module check,
    which is why :func:`_imported_module_targets` exists for the checks where it is not.
    """
    names: List[Tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.append((node.module, node.lineno))
            for alias in node.names:
                names.append((f"{node.module}.{alias.name}", node.lineno))
    return names


def _imported_module_targets(tree: ast.AST) -> List[Tuple[str, int]]:
    """Every module this file imports **from**, without the imported-symbol names."""
    names: List[Tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.append((node.module, node.lineno))
    return names


def _symbols_imported_from(tree: ast.AST, module: str) -> Set[str]:
    """The names a file imports out of one module."""
    symbols: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == module and not node.level:
            symbols.update(alias.name for alias in node.names)
    return symbols


def _numpy_aliases(tree: ast.AST) -> Set[str]:
    """Every local name bound to ``numpy`` or to a submodule of it."""
    aliases: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "numpy":
                    aliases.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] == "numpy":
                for alias in node.names:
                    aliases.add(alias.asname or alias.name)
    return aliases


def _numpy_random_uses(tree: ast.AST) -> List[int]:
    """Line numbers of every ``<numpy alias>.random`` attribute access.

    Also catches ``from numpy import random`` and ``from numpy.random import normal``, because
    :func:`_numpy_aliases` binds the imported name itself in those forms and the name ``random``
    among them is reported here directly.
    """
    aliases = _numpy_aliases(tree)
    lines: List[int] = []
    if not aliases:
        return lines
    if "random" in aliases:
        # ``from numpy import random`` - the alias IS the offence, no attribute access needed.
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".")[0] == "numpy":
                    lines.append(node.lineno)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "random"
            and isinstance(node.value, ast.Name)
            and node.value.id in aliases
        ):
            lines.append(node.lineno)
    return lines


# ══════════════════════════════════════════════════════════════════════════
#  THE ASSERTIONS
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("path", _paper_modules(), ids=lambda p: p.name)
def test_no_paper_module_imports_random(path: Path) -> None:
    """No ``import random`` and no ``from random import ...``, in any spelling.

    One test per module, so a failure names the file in the test id before its message is read.

    Requirements 14.9 (no synthesised, interpolated or randomised price), 16.12 (no randomised
    fill probability, fee or slippage), 15.4 (the same events replay to the same fills) and 18.1
    (exact decimal arithmetic) all rest on this. Each of them is a claim about what the paper
    package computes, and every one of them is false the moment a value here comes from a draw.
    """
    offenders = [
        lineno for root, lineno in _imported_roots(_tree(path)) if root == "random"
    ]
    assert offenders == [], (
        f"{path.name} imports the standard-library `random` at line(s) {offenders}. No module "
        f"under {PAPER_PACKAGE.name}/ may reach a randomised value: a paper price, fee, slippage "
        f"or fill probability drawn from a random source is a track record of a market that never "
        f"traded (Requirements 14.9, 16.12, 28.3), and it breaks the replay determinism of "
        f"Requirement 15.4. If a nonce or an id is what is needed, use `uuid` or `secrets`."
    )


@pytest.mark.parametrize("path", _paper_modules(), ids=lambda p: p.name)
def test_no_paper_module_reaches_numpy_random(path: Path) -> None:
    """No ``numpy.random`` attribute access, under any alias.

    ``numpy`` is a dependency of this repository and cannot be banned, so the check is on the
    ``.random`` attribute rather than on the import. ``np.random.normal(...)`` is the same
    fabricated price as ``random.gauss(...)`` reached by a different name.
    """
    offenders = _numpy_random_uses(_tree(path))
    assert offenders == [], (
        f"{path.name} reaches numpy.random at line(s) {offenders}. numpy.random is the same "
        f"randomness `random` is, under a different name, and every requirement the sibling test "
        f"cites applies to it identically."
    )


@pytest.mark.parametrize("path", _paper_modules(), ids=lambda p: p.name)
def test_no_paper_module_imports_the_forbidden_simulator(path: Path) -> None:
    """No import of ``backend_app.backend.exchange_simulator``, in any spelling.

    Requirement 13.8 confines that module to internal staging self-tests: no Paper_Trading_API
    request path and no Paper_Session execution path may reach it. This is the half of task 25.1
    that closes the gap ``assert_paper_simulator`` cannot - a subclass, or a call that never goes
    through the guard - because it fails on the *import*, before anything can be done with it.

    Importing it would also execute its module body, whose line 49 is ``import random``, which is
    precisely why the runtime guard compares a tuple of strings instead.
    """
    offenders = [
        lineno
        for name, lineno in _imported_modules(_tree(path))
        if name == FORBIDDEN_MODULE or name.startswith(f"{FORBIDDEN_MODULE}.")
    ]
    assert offenders == [], (
        f"{path.name} imports {FORBIDDEN_MODULE} at line(s) {offenders}. Its prices are "
        f"random.gauss (lines 319, 753) and its fills are "
        f"random.random() > self.fill_probability (line 369), so it is confined to staging "
        f"self-tests (Requirements 13.8, 13.11). Importing it also runs its `import random` on "
        f"line 49, which is why paper_simulator.assert_paper_simulator compares "
        f"(__module__, __qualname__) as strings and never imports the module to check it."
    )


@pytest.mark.parametrize("path", _paper_modules(), ids=lambda p: p.name)
def test_no_paper_module_imports_a_module_by_computed_name(path: Path) -> None:
    """No ``importlib.import_module`` and no ``__import__``.

    The one hole a static import walk has: a name assembled at runtime is invisible to it. Closed
    by refusing dynamic import in this package outright. The package has no need for one - every
    lazy import it does make (``core.audit_trail``, and ``paper_channel`` from ``paper_events``) is
    a plain ``from ... import ...`` inside a function, which this walk sees and checks like any
    other.
    """
    offenders: List[int] = []
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "__import__":
                offenders.append(node.lineno)
            elif isinstance(func, ast.Attribute) and func.attr == "import_module":
                offenders.append(node.lineno)
    assert offenders == [], (
        f"{path.name} imports a module by computed name at line(s) {offenders}. A dynamic import "
        f"is invisible to this walk, so it would let `random` - or "
        f"{FORBIDDEN_MODULE} - into the paper package past every check in this file. Use a plain "
        f"`from x import y`, inside the function if it must be lazy."
    )


@pytest.mark.parametrize("path", _paper_modules(), ids=lambda p: p.name)
def test_every_first_party_import_of_the_paper_package_is_enumerated(path: Path) -> None:
    """The package's first-party dependency list is pinned.

    The transitive half of "no ``random`` under the paper package, directly or transitively"
    cannot be decided by parsing this package alone. So it is bounded instead: every
    ``backend_app.*`` module the package imports must be in
    :data:`PERMITTED_FIRST_PARTY_IMPORTS`, each of which was read and is free of a randomness
    draw on the paper path. Adding a dependency therefore means editing this list, which puts the
    question in front of a reviewer rather than leaving it to be discovered later.
    """
    unlisted = sorted(
        {
            name
            for name, _ in _imported_module_targets(_tree(path))
            if name.startswith("backend_app.") and name not in PERMITTED_FIRST_PARTY_IMPORTS
        }
    )
    assert unlisted == [], (
        f"{path.name} imports first-party module(s) {unlisted}, which are not in "
        f"PERMITTED_FIRST_PARTY_IMPORTS. That list is how the transitive half of the no-random "
        f"guarantee is bounded: add the module only after reading it and confirming it draws no "
        f"random value on any path the paper package reaches, and say so in the comment above "
        f"the list."
    )


def test_the_paper_package_imports_only_the_validators_from_market_data_validation() -> None:
    """The one permitted dependency that does import ``random``, pinned to three symbols.

    ``backend_app.backend.market_data_validation`` imports ``random`` and uses it in exactly two
    places, neither of which the paper package can reach:

    * ``GapHandler._synthetic_fill`` - its first statement is
      ``raise RuntimeError("_synthetic_fill is deprecated. Synthetic data is prohibited.")``, so
      every draw below it is unreachable.
    * ``_generate_sample_data`` - a sample-data helper, not on any validation path.

    Requirement 14.2 requires the paper feed to use the platform's existing validation rather than
    a second one, so the dependency stays. What is pinned instead is *which* symbols cross the
    boundary: the two validators and the exception. An import of the gap handler from this package
    fails here, which is the whole point - the reachability argument above stops being true the
    moment a fourth symbol is imported, and nothing else would notice.
    """
    module = "backend_app.backend.market_data_validation"
    for path, tree in _parsed().items():
        symbols = _symbols_imported_from(tree, module)
        unexpected = sorted(symbols - PERMITTED_VALIDATION_SYMBOLS)
        assert unexpected == [], (
            f"{path.name} imports {unexpected} from {module}, which is outside the pinned set "
            f"{sorted(PERMITTED_VALIDATION_SYMBOLS)}. That module imports `random`; the argument "
            f"that the paper package cannot reach the draw rests on exactly which symbols are "
            f"imported from it. Re-establish the argument before widening the set."
        )


def test_the_deprecated_synthetic_fill_still_refuses_before_it_draws() -> None:
    """The other half of that argument, checked against the source rather than assumed.

    ``_synthetic_fill``'s ``raise`` must remain the first statement in its body. If it were ever
    removed, the ``random.gauss`` and ``random.random()`` calls beneath it would become live code
    on a module the paper feed imports - and no test in this file walks *that* package.
    """
    source = (
        REPO_ROOT / "backend_app" / "backend" / "market_data_validation.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    found = False
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
            node.name == "_synthetic_fill"
        ):
            found = True
            body = [
                statement
                for statement in node.body
                if not (
                    isinstance(statement, ast.Expr)
                    and isinstance(statement.value, ast.Constant)
                )
            ]
            assert body and isinstance(body[0], ast.Raise), (
                "market_data_validation.GapHandler._synthetic_fill no longer raises as its first "
                "statement, so its random.gauss / random.random() calls are now reachable on a "
                "module the paper feed imports (Requirements 14.9, 28.3)."
            )
    assert found, "market_data_validation._synthetic_fill has been renamed or removed"


def test_the_walk_actually_covers_the_whole_package() -> None:
    """The guard is only as good as the file list, so the file list is asserted.

    A glob that matched nothing, or that missed the module the simulator lives in, would make
    every test above pass by walking nothing. Both are checked here: the eight modules the
    package holds today are named, and ``paper_simulator.py`` among them.
    """
    names = {path.name for path in _paper_modules()}
    assert "paper_simulator.py" in names
    assert "__init__.py" in names
    assert {
        "__init__.py",
        "errors.py",
        "paper_accounting.py",
        "paper_channel.py",
        "paper_events.py",
        "paper_market_feed.py",
        "paper_order_state.py",
        "paper_replay.py",
        "paper_repository.py",
        "paper_session_service.py",
        "paper_simulator.py",
    } <= names, f"the paper package lost a module this walk was covering; found {sorted(names)}"


def test_the_narrower_predecessor_is_a_proper_subset_of_this_module() -> None:
    """``test_paper_market_feed_selection``'s check is contained by this one, and stays so.

    The stated relationship between the two files, asserted rather than left in a comment. The
    feed suite's ``test_no_module_in_the_paper_package_imports_random`` walks the same package for
    the two ``import random`` forms; this module walks the same package for those forms plus
    dotted and aliased spellings, ``numpy.random``, the ``exchange_simulator`` import and dynamic
    imports.

    Containment is checked the only way that is meaningful: by running the predecessor's own
    detector over the same file list and requiring it to find nothing that this module's detector
    would not also find. If the feed check is ever widened past this module's scope, this fails -
    which is the point, because at that moment "authoritative" would have moved and the comment
    at the top of this file would be wrong.
    """
    for path, tree in _parsed().items():
        # The predecessor's exact detector, transcribed.
        predecessor: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(
                alias.name.split(".")[0] == "random" for alias in node.names
            ):
                predecessor.append(path.name)
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.split(".")[0] == "random"
            ):
                predecessor.append(path.name)

        mine = [lineno for root, lineno in _imported_roots(tree) if root == "random"]

        assert bool(predecessor) <= bool(mine), (
            f"{path.name}: the narrower check in tests/test_paper_market_feed_selection.py "
            f"found an offence this module did not. This module is documented as authoritative "
            f"and strictly broader, so either its detector has a gap or the other one has been "
            f"widened; resolve which before either is trusted."
        )
