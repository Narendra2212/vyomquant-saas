"""Debug why `table`/`column`/`on_delete` are not recognised as constrained.

Imports the checker's own helpers and reports, per scope of the failing migration, which
names each scope marks constrained vs unconstrained -- so the gap is measured, not guessed.
"""
from __future__ import annotations

import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import importlib.util

spec = importlib.util.spec_from_file_location(
    "bandit_gate", ROOT / "tests" / "test_bandit_sast_gate_is_green.py"
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

target = ROOT / "backend_app" / "alembic" / "versions" / \
    "d4e5f6a7b8c9_bug049_normalize_user_id_to_uuid.py"
tree = ast.parse(target.read_text(encoding="utf-8"))

# Seed module-level literal sequences the way the test does before analysing scopes.
mod._MODULE_LITERAL_SEQUENCES = mod._module_literal_sequences(tree)
print("module literal sequences:", sorted(mod._MODULE_LITERAL_SEQUENCES))

for scope in mod._scopes(tree):
    name = getattr(scope, "name", "<module>")
    constrained = mod._constrained_names_in_scope(scope)
    for interesting in ("table", "column", "on_delete", "target", "name"):
        if interesting in constrained:
            marker = "CONSTRAINED"
        else:
            marker = "not-constrained"
        # Only report scopes that actually bind the name, to keep it readable.
        binds = any(
            isinstance(n, (ast.Assign, ast.For, ast.AsyncFor))
            for n in ast.walk(scope)
        )
    print(f"scope={name:30s} constrained={sorted(n for n in constrained if n in ('table','column','on_delete','target','name'))}")
