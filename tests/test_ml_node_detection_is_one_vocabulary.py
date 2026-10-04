"""
tests/test_ml_node_detection_is_one_vocabulary.py

Four gates ask "does this strategy contain a model node, and therefore need a trained
model?" and three of them ask it with a narrower vocabulary than the fourth, so a node
typed ``ml_dl`` or ``ml_model`` satisfies one gate and is invisible to the other three.

THE FOUR SITES
``backend_app/routers/strategies.py`` declares the full set and uses it::

    _LEGACY_ML_NODE_TYPES: frozenset = frozenset({"ml", "dl", "ml_dl", "ml_model"})

    def _detect_ml_nodes(nodes):
        ...
        if node_type in _LEGACY_ML_NODE_TYPES:

The other three each inline a two-element list:

    backend_app/backend/strategy_service.py   deploy_strategy
    backend_app/routers/library.py            clone_strategy
    backend_app/tee/main.py                   the TEE start gate

        ml_nodes = [n for n in nodes if n.get("type", "").lower() in ["ml", "dl"]]

Each of those four then refuses when it finds model nodes and the strategy has no
``ml_model_path``. So a strategy whose model node is spelled ``ml_dl`` is refused by the
router's deploy and resume paths and waved through by the service's deploy path, the
library clone path and the TEE start path -- three of the four.

WHY THE SUPERSET IS THE ANSWER, AND WHY IT IS SAFE
The router's set is not arbitrary: the comment beside it explains that these are the
schema-version-1 ``node["type"]`` spellings denoting a model block, kept explicit so
``_detect_ml_nodes`` reads stored v1 blueprints byte-for-byte, and that the canonical
vocabulary folds all four into the single ``ML_DL`` category. All four spellings mean
"this is a model node". A gate that misses one is simply wrong.

Unifying on the superset can only ever cause MORE refusal, never less: the predicate is
membership in a set, the set grows, so every strategy refused before is still refused and
some additionally are. Nothing becomes deployable that was not. The strategies newly
refused are exactly those carrying a model node with no model, which is the condition all
four gates exist to catch -- and the router already refuses them on its own paths, so the
inconsistency is what is novel, not the refusal.

ONE SHARED DEFINITION, BELOW THE ROUTERS
The set now lives in ``backend_app/backend/ml_node_types.py``, which the routers, the
service and the TEE all import. It could not stay in ``routers/strategies.py``:
``strategy_service`` is imported BY the routers, so a router-level constant would invert
the dependency, and ``tee/main.py`` has no business importing a FastAPI router at all. It
is a module with no heavy imports on purpose -- ``ml_models`` would have been the topical
home, but it probes for tensorflow, joblib and friends at import time, and a clone or a
TEE start should not pay for that to answer a question about four strings.
"""

from __future__ import annotations

import ast
import inspect
import os
import pathlib
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

#: The spellings that denote a model block, as the router has always defined them.
EXPECTED_SPELLINGS = frozenset({"ml", "dl", "ml_dl", "ml_model"})

#: Every place that asks the "is this a model node?" question.
GATE_SITES = (
    "backend_app/routers/strategies.py",
    "backend_app/backend/strategy_service.py",
    "backend_app/routers/library.py",
    "backend_app/tee/main.py",
)

ROOT = pathlib.Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# 1. There is one definition, and it is the superset
# ---------------------------------------------------------------------------


class TestThereIsOneSharedVocabulary:
    def test_the_shared_module_exists_and_declares_the_superset(self):
        from backend_app.backend import ml_node_types as mnt

        assert mnt.ML_NODE_TYPES == EXPECTED_SPELLINGS, (
            f"the shared vocabulary is {set(mnt.ML_NODE_TYPES)}, expected "
            f"{set(EXPECTED_SPELLINGS)}"
        )

    def test_it_is_a_frozenset_so_a_caller_cannot_narrow_it(self):
        from backend_app.backend import ml_node_types as mnt

        assert isinstance(mnt.ML_NODE_TYPES, frozenset), (
            "a mutable set could be narrowed in place by any importer"
        )

    def test_it_exposes_one_detector(self):
        from backend_app.backend import ml_node_types as mnt

        nodes = [
            {"id": "a", "type": "ml"},
            {"id": "b", "type": "DL"},
            {"id": "c", "type": "ml_dl"},
            {"id": "d", "type": "ml_model"},
            {"id": "e", "type": "indicator"},
            {"id": "f"},
        ]

        found = {node["id"] for node in mnt.detect_ml_nodes(nodes)}

        assert found == {"a", "b", "c", "d"}, (
            f"the detector found {found}; it must match all four spellings, "
            f"case-insensitively, and nothing else"
        )

    def test_the_detector_tolerates_junk(self):
        """``nodes`` comes from stored JSONB that nothing validates."""
        from backend_app.backend import ml_node_types as mnt

        assert mnt.detect_ml_nodes([]) == []
        assert mnt.detect_ml_nodes(None) == []
        assert mnt.detect_ml_nodes(["not a dict", 7, None]) == []
        assert mnt.detect_ml_nodes([{"type": None}, {"type": 3}]) == []

    def test_the_module_pulls_in_no_heavy_dependency(self):
        """It is imported by a clone path and by the TEE; it must stay cheap.

        ``ml_models`` was the topical home and is deliberately not used: it probes for
        tensorflow and joblib at import time.
        """
        from backend_app.backend import ml_node_types as mnt

        source = inspect.getsource(mnt)
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])

        forbidden = imported & {
            "numpy",
            "pandas",
            "tensorflow",
            "joblib",
            "sklearn",
            "fastapi",
            "backend_app",
        }
        assert not forbidden, (
            f"ml_node_types imports {forbidden}; it is on the clone and TEE paths and "
            f"must stay dependency-free"
        )


# ---------------------------------------------------------------------------
# 2. No gate inlines its own narrower list any more
# ---------------------------------------------------------------------------


def _inlined_narrow_lists(path: pathlib.Path) -> list:
    """Call sites comparing a node type against a literal two-element list.

    Found structurally: an ``ast.Compare`` using ``In`` whose comparator is a literal
    list/set/tuple of strings that is a strict subset of the full vocabulary. A text
    search would match this file's own docstring and the explanatory comments left at
    each site.
    """
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        if not node.ops or not isinstance(node.ops[0], ast.In):
            continue
        comparator = node.comparators[0]
        if not isinstance(comparator, (ast.List, ast.Set, ast.Tuple)):
            continue
        values = {
            element.value
            for element in comparator.elts
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        }
        if not values or len(values) != len(comparator.elts):
            continue
        # Only flag lists that are clearly this vocabulary, partially spelled.
        if values & {"ml", "dl"} and values < EXPECTED_SPELLINGS:
            offenders.append((node.lineno, sorted(values)))
    return offenders


@pytest.mark.parametrize("relative", GATE_SITES)
def test_no_gate_inlines_a_narrower_vocabulary(relative):
    path = ROOT / relative
    assert path.is_file(), f"{relative} not found"

    offenders = _inlined_narrow_lists(path)

    assert not offenders, (
        f"{relative} still compares a node type against a partial vocabulary at "
        f"{offenders}; a node typed 'ml_dl' or 'ml_model' is a model node and this gate "
        f"would not see it. Import ML_NODE_TYPES / detect_ml_nodes from "
        f"backend_app.backend.ml_node_types instead."
    )


@pytest.mark.parametrize("relative", GATE_SITES)
def test_every_gate_uses_the_shared_definition(relative):
    """Positive counterpart: each site must actually reference the shared names."""
    source = (ROOT / relative).read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(source)

    referenced = {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    } | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }

    assert referenced & {"ML_NODE_TYPES", "detect_ml_nodes", "_detect_ml_nodes"}, (
        f"{relative} does not reference the shared ML node vocabulary"
    )


# ---------------------------------------------------------------------------
# 3. The behavioural consequence, at the gate that had the narrow list
# ---------------------------------------------------------------------------


class TestAnMlDlNodeIsSeenEverywhere:
    @pytest.mark.parametrize("spelling", sorted(EXPECTED_SPELLINGS))
    def test_the_router_detector_sees_every_spelling(self, spelling):
        from backend_app.routers import strategies as sr

        found = sr._detect_ml_nodes([{"id": "n1", "type": spelling}])

        assert len(found) == 1, f"the router's detector missed {spelling!r}"

    @pytest.mark.parametrize("spelling", sorted(EXPECTED_SPELLINGS))
    def test_the_router_refuses_a_model_node_with_no_model_path(self, spelling):
        """The refusal all four gates exist for, across all four spellings."""
        from fastapi import HTTPException

        from backend_app.routers import strategies as sr

        with pytest.raises(HTTPException) as caught:
            sr._validate_ml_models_present(
                {"nodes": [{"id": "n1", "type": spelling}], "ml_model_path": None}
            )

        assert caught.value.status_code == 400
        assert caught.value.detail["error"] == "ML_MODEL_MISSING"

    def test_unifying_only_ever_refuses_more(self):
        """The property that makes this change safe to land without a product call.

        The gates' predicate is membership in a set. Widening the set cannot turn a
        refusal into an acceptance, so nothing becomes deployable that was not.
        """
        from backend_app.backend import ml_node_types as mnt

        previously_detected = {"ml", "dl"}

        assert previously_detected <= mnt.ML_NODE_TYPES, (
            "the shared vocabulary no longer contains the spellings the narrow lists "
            "matched, so this change could make something deployable that was refused "
            "before -- which is the opposite of the intent"
        )


# ---------------------------------------------------------------------------
# 4. One of the four gates is unreachable, and this file must not overclaim
# ---------------------------------------------------------------------------


class TestTheTeeGateIsUnreachable:
    """``backend_app/tee/main.py`` cannot be imported, so its gate never runs.

    Found while verifying the unification above, and recorded here because without it
    this file would claim four gates now agree when only three of them are reachable.

    ``tee/main.py`` line 11 is::

        from backend_app.core.fleet_manager import get_fleet_manager

    ``backend_app/core/fleet_manager.py`` does not exist -- the module lives at
    ``backend_app/backend/fleet_manager.py`` -- and ``get_fleet_manager`` is not defined
    there either, so there is no correct target to repoint the import at. Importing the
    module raises ``ModuleNotFoundError`` before any of its code runs, and no test
    anywhere imports ``backend_app.tee``.

    NOT FIXED. This is not a mistyped path with an obvious destination: supplying
    ``get_fleet_manager`` means deciding what the TEE's fleet handle should be, which is
    reconstructing a process entrypoint that publishes ``start_bot`` commands. That is
    feature work on an execution path, not a cleanup. The vocabulary change made to that
    file is correct and takes effect the moment the module becomes importable.

    ``routers/strategies.py`` reads ``USE_TEE`` and publishes a ``start_bot`` command when
    it is set, so a deployment with ``USE_TEE=true`` has a producer and no consumer.
    """

    def test_the_tee_module_cannot_be_imported(self):
        import importlib

        with pytest.raises(ModuleNotFoundError) as caught:
            importlib.import_module("backend_app.tee.main")

        assert "fleet_manager" in str(caught.value), (
            f"backend_app.tee.main now fails to import for a different reason: "
            f"{caught.value}. If it imports cleanly, delete this test -- its gate is "
            f"reachable and the unification above covers four live gates, not three."
        )

    def test_the_module_it_imports_does_not_exist(self):
        assert not (ROOT / "backend_app" / "core" / "fleet_manager.py").is_file(), (
            "backend_app/core/fleet_manager.py now exists; re-check whether the TEE "
            "gate is reachable"
        )

    def test_and_the_symbol_does_not_exist_at_the_other_path_either(self):
        """So there is no one-line repoint available, which is why this is not fixed."""
        backend_fleet = ROOT / "backend_app" / "backend" / "fleet_manager.py"
        assert backend_fleet.is_file(), "backend_app/backend/fleet_manager.py is gone"

        tree = ast.parse(backend_fleet.read_text(encoding="utf-8", errors="replace"))
        names = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        } | {
            target.id
            for node in tree.body
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }

        assert "get_fleet_manager" not in names, (
            "get_fleet_manager now exists in backend_app/backend/fleet_manager.py, so "
            "the TEE import has a correct target and repointing it is a real fix"
        )

    def test_the_tee_file_still_uses_the_shared_vocabulary(self):
        """The change is correct and dormant; it must not be reverted as 'dead code'."""
        source = (ROOT / "backend_app" / "tee" / "main.py").read_text(
            encoding="utf-8", errors="replace"
        )
        assert "detect_ml_nodes" in source
        assert not _inlined_narrow_lists(ROOT / "backend_app" / "tee" / "main.py")
