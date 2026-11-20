"""SEC-05 -- "MFA required only for exchange key changes; admin/operator actions accept
aal1" -- cross-referenced against SEC-15's fix, with a repo-wide sweep SEC-15's own test
file (test_role_granularity_and_audit.py::TestSec15AdminMutationsRequireMfa) does not do.

THE FINDING AND WHERE IT WAS ACTUALLY FIXED
    SEC-05 and SEC-15 are the same underlying defect tracked under two different ids by
    two different audit passes (SEC-15 is P1, SEC-05 is P2 -- kept separate per this
    session's standing rule to preserve every original finding id rather than silently
    merge them). SEC-15's fix added ``Depends(require_aal2)`` to the three
    ``get_operator_user``-gated mutating routes in ``routers/admin.py``
    (``set_user_status``, ``global_kill_switch`` / kill-all, ``reinitialize_platform``),
    which is exactly what SEC-05's own remaining_action asked for: "Depend on
    require_aal2 for admin/operator routes."

    ``get_operator_user``'s own docstring names its scope precisely: "High-blast-radius
    actions: set_user_status, global_kill_switch" (reinitialize was added alongside them,
    same class of action -- clearing a platform-wide halt). The broader
    ``get_admin_user`` dependency (admin, support OR operator role; lower-blast-radius
    reads and moderation actions -- health, user listing, ticket replies, strategy
    moderation, worker start/stop) is deliberately NOT required to carry MFA under the
    existing security contract, the same "do not apply globally" principle
    ``require_aal2``'s own docstring states. Billing mutations and a password-change path
    are a separate, explicitly-deferred product decision recorded under SEC-15, not
    re-litigated here.

WHAT THIS FILE PROVES, THAT THE EXISTING SEC-15 TESTS DO NOT
    SEC-15's own tests pin the three known routes individually. This file instead sweeps
    EVERY router module in ``backend_app/routers/`` by AST for any handler whose
    parameters include ``Depends(get_operator_user)``, and asserts every one of them ALSO
    depends on ``require_aal2`` -- so a future operator-gated route added anywhere in the
    codebase (not just admin.py) cannot silently ship without MFA the way the original
    three did before SEC-15.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
ROUTERS_DIR = REPO_ROOT / "backend_app" / "routers"


def _depends_target(default: ast.expr) -> str | None:
    """For a parameter default of the form Depends(x), return x's name."""
    if not (isinstance(default, ast.Call) and getattr(default.func, "id", "") == "Depends"):
        return None
    if not default.args:
        return None
    arg = default.args[0]
    if isinstance(arg, ast.Name):
        return arg.id
    if isinstance(arg, ast.Attribute):
        return arg.attr
    return None


def _function_dependency_names(fn: ast.AST) -> set[str]:
    names: set[str] = set()
    args = fn.args  # type: ignore[attr-defined]
    all_args = list(args.args) + list(args.kwonlyargs)
    all_defaults = list(args.defaults) + list(args.kw_defaults)
    # Python's ast pairs positional defaults with the TAIL of args.args; kw_defaults
    # pairs 1:1 with kwonlyargs (None for a required kwonly). Walk both properly.
    pos_args = list(args.args)
    pos_defaults = list(args.defaults)
    offset = len(pos_args) - len(pos_defaults)
    for i, default in enumerate(pos_defaults):
        target = _depends_target(default)
        if target:
            names.add(target)
    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        if default is None:
            continue
        target = _depends_target(default)
        if target:
            names.add(target)
    return names


def _routes_depending_on(dependency_name: str) -> list[tuple[Path, str, set[str]]]:
    """Every (file, function_name, all_dependency_names) for a handler whose parameters
    include Depends(<dependency_name>), across every router module.
    """
    results = []
    for py_file in sorted(ROUTERS_DIR.glob("*.py")):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                deps = _function_dependency_names(node)
                if dependency_name in deps:
                    results.append((py_file, node.name, deps))
    return results


class TestEveryOperatorGatedRouteAlsoRequiresMfa:
    def test_at_least_the_three_known_sec15_routes_are_found(self):
        """Sanity check on the AST walk itself before trusting its negative result."""
        found = _routes_depending_on("get_operator_user")
        names = {name for _, name, _ in found}
        assert {"set_user_status", "global_kill_switch", "reinitialize_platform"} <= names

    def test_every_operator_gated_handler_also_depends_on_require_aal2(self):
        found = _routes_depending_on("get_operator_user")
        assert found, "no operator-gated routes found at all -- the AST walk likely broke"

        missing = [
            (str(path.relative_to(REPO_ROOT)), name)
            for path, name, deps in found
            if "require_aal2" not in deps
        ]
        assert not missing, (
            f"SEC-05/SEC-15 REGRESSION: these operator-gated (high-blast-radius) routes "
            f"do not require MFA: {missing}"
        )

    def test_get_operator_user_is_only_used_inside_admin_py_today(self):
        """Not a requirement in itself -- a NEGATIVE CONTROL recording today's scope, so a
        future PR that adds a second operator-gated router is a deliberate, reviewed
        decision rather than a silent expansion the test above would still catch anyway.
        """
        found = _routes_depending_on("get_operator_user")
        files = {path.name for path, _, _ in found}
        assert files == {"admin.py"}
