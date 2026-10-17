"""
tests/test_ml_node_detection_is_one_vocabulary.py

Three gates ask "does this strategy contain a model node, and therefore need a trained
model?" and two of them ask it with a narrower vocabulary than the third, so a node
typed ``ml_dl`` or ``ml_model`` satisfies one gate and is invisible to the other two.

(A fourth site, ``backend_app/tee/main.py``'s own TEE start gate, existed when this file
was first written. RT-23 deleted that module as confirmed-dead code -- it crashed on
import and was never actually deployed, see its own ledger entry -- so it is no longer
one of the sites this file has to keep in agreement. ``TestTheTeeGateIsUnreachable``
below now documents the deletion rather than an import failure.)

THE THREE SITES
``backend_app/routers/strategies.py`` declares the full set and uses it::

    _LEGACY_ML_NODE_TYPES: frozenset = frozenset({"ml", "dl", "ml_dl", "ml_model"})

    def _detect_ml_nodes(nodes):
        ...
        if node_type in _LEGACY_ML_NODE_TYPES:

The other two each inline a two-element list:

    backend_app/backend/strategy_service.py   deploy_strategy
    backend_app/routers/library.py            clone_strategy

        ml_nodes = [n for n in nodes if n.get("type", "").lower() in ["ml", "dl"]]

Each of those three then refuses when it finds model nodes and the strategy has no
``ml_model_path``. So a strategy whose model node is spelled ``ml_dl`` is refused by the
router's deploy and resume paths and waved through by the service's deploy path and the
library clone path -- two of the three.

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
The set now lives in ``backend_app/backend/ml_node_types.py``, which the routers and the
service both import. It could not stay in ``routers/strategies.py``: ``strategy_service``
is imported BY the routers, so a router-level constant would invert the dependency. It
is a module with no heavy imports on purpose -- ``ml_models`` would have been the topical
home, but it probes for tensorflow, joblib and friends at import time, and a clone should
not pay for that to answer a question about four strings.
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
#: backend_app/tee/main.py was a fourth site until RT-23 deleted it as confirmed-dead
#: code (see TestTheTeeGateIsUnreachable below for why) -- removed from this tuple
#: along with it, since a parametrized check against a path that no longer exists
#: would fail for the wrong reason.
GATE_SITES = (
    "backend_app/routers/strategies.py",
    "backend_app/backend/strategy_service.py",
    "backend_app/routers/library.py",
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
# 4. The fourth gate is gone, not merely unreachable -- RT-23
# ---------------------------------------------------------------------------


class TestTheTeeGateIsUnreachable:
    """``backend_app/tee/main.py`` no longer exists.

    Originally recorded here as "cannot be imported": ``tee/main.py`` line 11 called
    ``from backend_app.core.fleet_manager import get_fleet_manager``, a module and symbol
    that never existed, so importing it raised ``ModuleNotFoundError`` before any of its
    code (including the vocabulary fix the rest of this file is about) ever ran.

    RT-23 investigated further and found the import failure was the SMALLER problem.
    ``backend_app/workers/command_worker.py`` -- a separate, already-deployed, tested
    process -- consumes the exact same ``COMMAND_STREAM = 'command_queue'`` and handles
    the same ``start_bot``/``stop_bot`` actions ``tee/main.py`` would have. Redis streams
    let multiple consumer groups each read every message, so naively repairing the broken
    import (supplying a ``get_fleet_manager`` that did not exist) would not have made this
    gate reachable in isolation -- it would have made both processes independently
    consume every start_bot/stop_bot command, a live double-execution hazard against real
    exchange accounts, on top of being unauthorised feature work on an execution path.
    Confirmed via real AWS evidence (gathered the same session this deletion landed in)
    that no ECS service for it had ever actually existed in production either -- nothing
    was deployed to even carry the risk forward. So it was deleted: the module, its
    Dockerfile (``Dockerfile.tee``), and its Terraform ECS resources
    (``aws_ecs_task_definition.tee`` / ``aws_ecs_service.tee``).

    NOT rebuilt as a separate lane. That remains a real, deliberate design decision this
    repository's own standing mandate declines to make unilaterally: if a genuinely
    separate execution lane from ``command_worker.py`` is wanted later, it needs its own
    queue/consumer-group (never ``command_queue``) and the same safety wiring RT-15/16/21
    already gave the rest of live execution, not a one-line import repair.
    """

    def test_the_tee_module_no_longer_exists(self):
        assert not (ROOT / "backend_app" / "tee").exists(), (
            "backend_app/tee has reappeared. If it was reintroduced as a genuinely "
            "separate execution lane (its own queue/consumer-group, not command_queue), "
            "update this test and this class's docstring to match; if it is the same "
            "dead module come back, delete it again."
        )

    def test_dockerfile_tee_no_longer_exists(self):
        assert not (ROOT / "Dockerfile.tee").is_file()

    def test_command_worker_is_the_one_live_consumer_of_the_command_queue(self):
        """The fact that made the TEE repair dangerous rather than merely incomplete:
        a second consumer of the same queue command_worker.py already serves."""
        worker = ROOT / "backend_app" / "workers" / "command_worker.py"
        assert worker.is_file(), (
            "backend_app/workers/command_worker.py is gone -- re-verify RT-23's "
            "reasoning before assuming TEE staying deleted is still safe to assert here"
        )
        source = worker.read_text(encoding="utf-8", errors="replace")
        assert "command_queue" in source or "COMMAND_STREAM" in source, (
            "command_worker.py no longer references the command queue by this name -- "
            "re-verify RT-23's reasoning"
        )
