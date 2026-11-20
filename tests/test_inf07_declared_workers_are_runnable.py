"""INF-07: every worker terraform declares a command for must be RUNNABLE, not just named.

WHAT THIS GUARDS, AND WHY THE EXISTING TEST DID NOT
===================================================
``tests/test_worker_deployment_coverage.py`` asserts that every ``WorkerBase`` subclass
appears in a ``python -m <module>`` string inside ``terraform/ecs.tf``. That is assurance
about a STRING. ``backend_app.backend.dag_worker`` satisfied it for the entire life of the
dag entry while being unable to run at all: the module had no
``if __name__ == "__main__"`` block, so ``python -m backend_app.backend.dag_worker``
imported it, built its module-level ``worker_pool`` global, reached the end of the file and
exited 0.

That is invisible until somebody deploys it, and then it is invisible again, because exit
code 0 is indistinguishable from a job that finished. MEASURED in production on
2026-10-25: two ECS tasks (c247dbe435aa43d8ad123a4368211763,
517076dbe99543009d5deb6354c1fd95) reached ``STOPPED`` with
``stoppedReason="Essential container in task exited"`` and ``exitCode=0``, and ECS
restarted the service each time.

So this file asserts the property the other one cannot: a declared command names a module
that can actually be run as a process.

WHY IT CHECKS SOURCE AND DOES NOT EXECUTE
=========================================
Running ``python -m <module>`` for real would start a worker that connects to Redis and
PostgreSQL, which a unit test must not do. The check is therefore static: the module is
parsed with ``ast`` and must contain a top-level ``if __name__ == "__main__":`` block.
Parsing rather than regex-matching means a ``__main__`` mentioned in a docstring or a
comment -- which is exactly what dag_worker's own explanatory comment now contains -- does
not satisfy it.
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
ECS_TF = REPO / "terraform" / "ecs.tf"

#: Every ``python -m <module>`` the deployment declares. Read from terraform rather than
#: listed here, so a worker added to ``local.worker_definitions`` is covered the moment it
#: is declared and nobody has to remember this file exists.
_MODULE_RE = re.compile(r"""python3?["',\s]+-m["',\s]+([A-Za-z_][\w.]*)""")


def declared_modules() -> list[str]:
    text = ECS_TF.read_text(encoding="utf-8")
    return sorted(set(_MODULE_RE.findall(text)))


def module_path(dotted: str) -> pathlib.Path:
    return REPO / (dotted.replace(".", "/") + ".py")


def has_main_block(path: pathlib.Path) -> bool:
    """Is there a top-level ``if __name__ == "__main__":``?

    Parsed, not grepped: dag_worker's own INF-07 comment quotes the string
    ``if __name__ == "__main__"`` while explaining its previous absence, and a regex would
    have accepted that comment as the fix.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not isinstance(test, ast.Compare) or len(test.comparators) != 1:
            continue
        left, right = test.left, test.comparators[0]
        if (isinstance(left, ast.Name) and left.id == "__name__"
                and isinstance(right, ast.Constant) and right.value == "__main__"):
            return True
    return False


def test_terraform_declares_at_least_one_worker_command():
    """A regression on the parser itself: an empty list would make everything below pass."""
    modules = declared_modules()
    assert modules, (
        f"no `python -m <module>` command was parsed out of {ECS_TF}. Either the file "
        f"moved or the shape of local.worker_definitions changed -- fix this parser rather "
        f"than letting the assertions below pass vacuously."
    )


@pytest.mark.parametrize("dotted", declared_modules())
def test_a_declared_worker_module_exists(dotted: str):
    path = module_path(dotted)
    assert path.exists(), (
        f"terraform/ecs.tf declares `python -m {dotted}` but {path.relative_to(REPO)} "
        f"does not exist. That command cannot start, and a container whose command cannot "
        f"be imported fails on every task placement."
    )


@pytest.mark.parametrize("dotted", declared_modules())
def test_a_declared_worker_is_runnable_as_a_process(dotted: str):
    """The INF-07 property: declared means runnable, not merely mentioned."""
    path = module_path(dotted)
    if not path.exists():
        pytest.skip("covered by test_a_declared_worker_module_exists")
    assert has_main_block(path), (
        f"terraform/ecs.tf declares `python -m {dotted}`, but "
        f"{path.relative_to(REPO)} has no top-level `if __name__ == \"__main__\":` "
        f"block.\n\n"
        f"`python -m` on such a module imports it and EXITS 0. ECS cannot tell that from "
        f"a job that completed, so it restarts the task forever while the service reports "
        f"a task running. This is INF-07, measured in production: dag_worker burned two "
        f"task lifecycles this way before anyone noticed.\n\n"
        f"Add a run_worker() plus a __main__ block in the shape "
        f"backend_app/workers/command_worker.py and "
        f"backend_app/backend/dag_worker.py use, or remove the entry from "
        f"local.worker_definitions. Declaring a command for a module that cannot run is "
        f"worse than not declaring it, because the deployment looks healthy."
    )


def test_dag_worker_specifically_is_fixed():
    """The module this finding is named for, asserted by name.

    The parametrised tests above cover it only while ecs.tf keeps declaring it. This one
    fails if dag_worker regresses even if somebody also removes its terraform entry, which
    would hide the regression from every other assertion in this file.
    """
    path = module_path("backend_app.backend.dag_worker")
    assert path.exists()
    assert has_main_block(path), (
        "backend_app/backend/dag_worker.py has lost its __main__ block again. This is the "
        "exact defect INF-07 records: the module defines DAGWorker and WorkerPool and a "
        "module-level worker_pool global, all of which import cleanly and none of which "
        "runs anything."
    )
    source = path.read_text(encoding="utf-8")
    assert "async def run_worker" in source, (
        "dag_worker lost run_worker(). The __main__ block alone is not the contract -- the "
        "three workers that already work (command, marketplace_expiry, and this one) all "
        "expose run_worker() so that hosting a worker has one shape."
    )
