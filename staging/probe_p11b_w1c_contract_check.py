"""Does the recovered source match the CURRENT shared-DSN safety contract?

PHASE 11B / WORKSTREAM 1C — Phase 0. Read-only; nothing is written and no server or
database is contacted.

WHY THIS RUNS BEFORE THE RESTORE
--------------------------------
Exact source for `staging/probe_pg_find.py` and `staging/pg_keepalive_r1.py` was recovered
from Kiro's editor local history, and the recovery is corroborated to the byte: each
file's size and mtime equal the `source_size_bytes` / `source_mtime` recorded in the
surviving `.pyc` header.

But "this is the exact file that was deleted" is not the same as "this file still fits the
tree". `staging/bootstrap_pg.py` has been edited since (mtime 12:32:32, i.e. AFTER the
keepalive's 12:18:55 and BEFORE the probe's 12:57:29). If the helper's surface changed,
restoring the old caller reintroduces a break rather than fixing one.

So this resolves every name the recovered modules reach for on the sibling helper, by AST,
against the helper as it exists right now. A missing name is a blocker, not a detail --
these are the functions that decide whether the shared DSN may be overwritten.

It also records which names are PRIVATE (leading underscore). A caller reaching into a
sibling's privates is a coupling worth naming in the report even when it resolves.
"""

from __future__ import annotations

import ast
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
HISTORY = pathlib.Path(
    r"C:\Users\aa\AppData\Roaming\Kiro\User\History")

RECOVERED = {
    "staging/probe_pg_find.py": HISTORY / "22750178" / "QBL9.py",
    "staging/pg_keepalive_r1.py": HISTORY / "-6acb9fd4" / "KgFq.py",
}

BOOTSTRAP = REPO / "staging" / "bootstrap_pg.py"

#: The alias each recovered module binds the helper module to.
HELPER_ALIASES = {"bp", "bootstrap"}


def _public_surface(path: pathlib.Path) -> dict[str, str]:
    """Top-level names a module exposes, with what kind of thing each is."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[node.name] = "function"
        elif isinstance(node, ast.ClassDef):
            bases = [ast.unparse(b) for b in node.bases]
            out[node.name] = f"class({', '.join(bases)})" if bases else "class"
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = "assignment"
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out[node.target.id] = "annotated assignment"
    return out


def _helper_attrs_used(path: pathlib.Path) -> dict[str, list[int]]:
    """`bp.X` / `bootstrap.X` attribute accesses, with the lines they occur on."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    used: dict[str, list[int]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                and node.value.id in HELPER_ALIASES:
            used.setdefault(node.attr, []).append(node.lineno)
    return used


def _dataclass_fields(path: pathlib.Path, class_name: str) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return [n.target.id for n in node.body
                    if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)]
    return []


def _state_attrs_used(path: pathlib.Path) -> set[str]:
    """Attributes read off whatever `probe(...)` returned -- the TargetState contract."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                and node.value.id == "state":
            out.add(node.attr)
    return out


def main() -> int:
    print("=" * 78)
    print("RECOVERED SOURCE vs CURRENT SHARED-DSN CONTRACT")
    print("=" * 78)

    for rel, src in RECOVERED.items():
        print(f"\n   {rel:34} recovered from {src.name}  "
              f"exists={src.exists()}  {src.stat().st_size if src.exists() else '-'}B")
    print(f"\n   helper: staging/bootstrap_pg.py  "
          f"{BOOTSTRAP.stat().st_size}B  mtime {BOOTSTRAP.stat().st_mtime}")

    surface = _public_surface(BOOTSTRAP)
    print(f"\n-- bootstrap_pg.py top-level surface ({len(surface)} names) -------------")
    for name in sorted(surface):
        print(f"     {name:28} {surface[name]}")

    blockers: list[str] = []
    privates: list[str] = []

    for rel, src in RECOVERED.items():
        if not src.exists():
            blockers.append(f"{rel}: recovered file missing")
            continue
        print(f"\n{'=' * 78}")
        print(f"{rel}")
        print("=" * 78)
        used = _helper_attrs_used(src)
        print(f"-- helper names it reaches for ({len(used)}) ------------------------")
        for attr in sorted(used):
            ok = attr in surface
            mark = "OK " if ok else "MISSING"
            kind = surface.get(attr, "-")
            print(f"     [{mark}] bp.{attr:26} {kind:22} lines {used[attr]}")
            if not ok:
                blockers.append(f"{rel}: bootstrap_pg has no `{attr}`")
            elif attr.startswith("_"):
                privates.append(f"{rel} -> bp.{attr}")

        states = _state_attrs_used(src)
        if states:
            fields = _dataclass_fields(BOOTSTRAP, "TargetState")
            methods = [n.name for n in ast.walk(ast.parse(
                BOOTSTRAP.read_text(encoding="utf-8")))
                if isinstance(n, ast.FunctionDef)]
            props = set(fields) | set(methods) | {"classification", "describe"}
            print(f"-- TargetState attributes it reads ({len(states)}) ------------------")
            print(f"     TargetState declared fields: {fields}")
            for attr in sorted(states):
                ok = attr in props
                print(f"     [{'OK ' if ok else 'MISSING'}] state.{attr}")
                if not ok:
                    blockers.append(f"{rel}: TargetState has no `{attr}`")

        # Third-party imports, which collection does not need but EXECUTION does.
        tree = ast.parse(src.read_text(encoding="utf-8"))
        third = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                third += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                third.append(node.module)
        external = sorted({m.split(".")[0] for m in third} - {
            "argparse", "importlib", "pathlib", "sys", "os", "time", "typing",
            "__future__", "ast", "json", "socket"})
        print(f"-- external imports: {external or 'none'}")
        for mod in external:
            import importlib.util as _u
            found = _u.find_spec(mod) is not None
            print(f"     {mod:16} installed={found}"
                  f"{'   <- EXECUTION would fail; collection is unaffected' if not found else ''}")

    print("\n" + "=" * 78)
    print("VERDICT")
    print("=" * 78)
    print(f"   contract blockers : {len(blockers)}")
    for item in blockers:
        print(f"     !! {item}")
    if not blockers:
        print("     none -- every name the recovered source needs exists today")
    print(f"   private coupling  : {len(privates)}")
    for item in privates:
        print(f"      - {item}")
    return 1 if blockers else 0


if __name__ == "__main__":
    sys.exit(main())
