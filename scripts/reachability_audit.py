#!/usr/bin/env python3
"""
scripts/reachability_audit.py

Automated AST Reachability Audit Tool
Traces imports starting from the main application entrypoint
and flags modules that are completely unreachable.
"""

import ast
import json
import os
import sys
from pathlib import Path
from typing import Set, List


def is_excluded(filepath: str) -> bool:
    """Check if the file is legitimately isolated from the main graph."""
    path_obj = Path(filepath)
    parts = path_obj.parts
    # Exclude directories
    if any(d in parts for d in ['tests', 'scripts', 'alembic']):
        return True
    
    filename = path_obj.name
    # Exclude test files, standalone validations
    if filename.endswith('_test.py') or filename.startswith('test_') or filename.endswith('_validation.py'):
        return True
    
    return False


def _add_with_ancestors(imports: Set[str], dotted: str) -> None:
    """Add a dotted module path AND every ancestor package path it implies.

    Real Python import semantics: `import a.b.c` (or `from a.b.c import X`)
    always loads a/__init__.py, then a/b/__init__.py, then a/b/c.py, in that
    order -- the parent packages are imported implicitly even though their
    own dotted names never appear literally in the source. The previous
    version of this function only recorded the literal dotted strings it
    found in the AST, so a file that writes `from backend_app.api.auth_router
    import router` was credited with reaching `backend_app.api.auth_router`
    but NOT with reaching `backend_app.api` or `backend_app` themselves --
    nothing in this codebase happens to write a bare `import backend_app` or
    `from backend_app import api`, so those package `__init__.py` files were
    flagged as unreachable dead code even though something imports a module
    three levels underneath them. Expanding every import into its full
    ancestor chain models the real loading behavior and fixes that whole
    false-positive class in one place, rather than special-casing
    `__init__.py` filenames in the reporting step.
    """
    if not dotted:
        return
    parts = dotted.split('.')
    for i in range(1, len(parts) + 1):
        imports.add('.'.join(parts[:i]))


#: Call targets that load a module named by a STRING rather than by syntax, and the
#: position of that string in the call's arguments.
#:
#: HY-02's remaining_action asked for exactly this: "should specifically re-verify
#: backend_app/backend/ for other sys.path/bare-import tricks like alert_engine's before
#: trusting the list". `backend_app/backend/alert_engine.py` was a CONFIRMED false positive
#: -- flagged dead by this tool while being loaded on every API boot -- because
#: `backend_app/core/state.py` does
#:
#:     _backend_path = os.path.join(os.path.dirname(__file__), "..", "backend")
#:     sys.path.insert(0, os.path.abspath(_backend_path))
#:     ...
#:     def safe_import(class_name, module_name):
#:         module = __import__(module_name, fromlist=[class_name])
#:     AlertEngine = safe_import("AlertEngine", "alert_engine")
#:
#: Two things defeat an AST scan at once: the module name is a bare top-level `alert_engine`
#: rather than `backend_app.backend.alert_engine`, and it only resolves because the file put
#: `backend_app/backend/` on sys.path first. Neither appears as an ast.Import node.
DYNAMIC_IMPORT_CALLS = {
    # builtin: __import__("name", fromlist=[...]) -- the name is argument 0
    "__import__": 0,
    # importlib.import_module("name") / import_module("name")
    "import_module": 0,
    # the local idiom in core/state.py: safe_import("ClassName", "module_name")
    # -- the module is the SECOND argument, which is why a naive "first string
    # literal in the call" heuristic would have recorded "AlertEngine" instead.
    "safe_import": 1,
}


def _literal_str(node) -> str:
    """The value of a string-literal AST node, or '' for anything else."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return ""


def _sys_path_dirs_added(tree, filepath: str) -> List[str]:
    """Directories the file puts on sys.path, where that is statically derivable.

    Only the shape this codebase actually uses is resolved:

        os.path.join(os.path.dirname(__file__), "..", "backend")

    i.e. `os.path.dirname(__file__)` followed by string literals, optionally wrapped in
    `os.path.abspath`/`os.path.normpath`. Anything else -- a name defined elsewhere, an
    environment variable, a computed value -- is NOT guessed at; the caller reports it as
    an unresolved dynamic import instead, so the tool's blind spot is visible rather than
    silent. Silence is what made alert_engine a false positive for two passes.
    """
    here = os.path.dirname(os.path.abspath(filepath))
    found: List[str] = []

    # Module-level simple assignments, so a path built into a local and THEN inserted is
    # followed. core/state.py does exactly that, and the first version of this function
    # missed it for that reason -- it only matched an os.path.join written inline inside
    # the sys.path.insert call:
    #
    #     _backend_path = os.path.join(os.path.dirname(__file__), "..", "backend")
    #     sys.path.insert(0, os.path.abspath(_backend_path))
    #
    # One level of indirection, and the whole mechanism was invisible again.
    assigned: dict = {}

    def _resolve(node) -> str:
        # A name previously assigned a resolvable path expression.
        if isinstance(node, ast.Name):
            return assigned.get(node.id, "")
        # os.path.abspath(X) / os.path.normpath(X) -- transparent for our purposes
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("abspath", "normpath") and node.args):
            return _resolve(node.args[0])
        # os.path.join(a, b, c)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "join"):
            parts = []
            for arg in node.args:
                if (isinstance(arg, ast.Call) and isinstance(arg.func, ast.Attribute)
                        and arg.func.attr == "dirname"):
                    # os.path.dirname(__file__)
                    inner = arg.args[0] if arg.args else None
                    if isinstance(inner, ast.Name) and inner.id == "__file__":
                        parts.append(here)
                        continue
                    return ""
                literal = _literal_str(arg)
                if not literal:
                    return ""
                parts.append(literal)
            return os.path.abspath(os.path.join(*parts)) if parts else ""
        return ""

    # Collect assignments first, so an insert() that references one resolves regardless of
    # the order ast.walk happens to visit them in.
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                value = _resolve(node.value)
                if value:
                    assigned[target.id] = value

    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in ("insert", "append"):
            continue
        # sys.path.insert(0, X) / sys.path.append(X)
        target = node.func.value
        is_sys_path = (
            isinstance(target, ast.Attribute) and target.attr == "path"
            and isinstance(target.value, ast.Name) and target.value.id == "sys"
        )
        if not is_sys_path:
            continue
        for arg in node.args:
            resolved = _resolve(arg)
            if resolved and os.path.isdir(resolved):
                found.append(resolved)
    return found


def extract_dynamic_imports(filepath: str, tree, base_dir: str):
    """Modules loaded by name-as-string, resolved against this file's own sys.path edits.

    Returns ``(dotted_imports, unresolved)``. ``unresolved`` holds
    ``(module_name, reason)`` for every dynamic import whose target could not be pinned
    down, so ``main()`` can print them. A dynamic import that cannot be resolved is a
    KNOWN blind spot; one that is never mentioned is an unknown one, and the second kind
    is how a live module gets reported as dead.
    """
    dotted: Set[str] = set()
    unresolved: List[tuple] = []
    extra_dirs = _sys_path_dirs_added(tree, filepath)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else (
            func.id if isinstance(func, ast.Name) else None)
        if name not in DYNAMIC_IMPORT_CALLS:
            continue

        position = DYNAMIC_IMPORT_CALLS[name]
        if len(node.args) <= position:
            continue
        module = _literal_str(node.args[position])
        if not module:
            unresolved.append((f"{name}(...)", "module name is not a string literal"))
            continue

        # Already a dotted path inside the package -- the ordinary case.
        if resolve_module_to_file(module, base_dir):
            _add_with_ancestors(dotted, module)
            continue

        # A bare top-level name, resolvable only because the file extended sys.path.
        placed = False
        for directory in extra_dirs:
            candidate = os.path.join(directory, *module.split(".")) + ".py"
            if not os.path.isfile(candidate):
                candidate = os.path.join(directory, *module.split("."), "__init__.py")
            if os.path.isfile(candidate):
                rel = os.path.relpath(candidate, base_dir)
                mod = rel[:-3].replace(os.sep, ".")
                if mod.endswith(".__init__"):
                    mod = mod[:-9]
                _add_with_ancestors(dotted, mod)
                placed = True
                break
        if not placed:
            unresolved.append((
                module,
                "bare name, and no sys.path directory this file adds contains it"
                if extra_dirs else
                "bare name, and this file adds no statically resolvable sys.path entry",
            ))
    return dotted, unresolved


#: Every dynamic import the scan could not pin down, accumulated across files so main()
#: can report the tool's own blind spot alongside the dead-code list.
UNRESOLVED_DYNAMIC_IMPORTS: List[tuple] = []


def extract_imports_from_file(filepath: str, module_name: str) -> Set[str]:
    """Extract all absolute and resolved-relative imports from a file."""
    imports = set()
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            tree = ast.parse(f.read(), filename=filepath)
    except Exception:
        return imports

    # Modules loaded by string name. Resolved against this file's own sys.path edits, so
    # core/state.py's four engines (api_key_vault, telemetry_engine, alert_engine,
    # fleet_manager) are credited to it the way Python actually credits them.
    # Walk up from the file's own directory to the package root. For module
    # `backend_app.core.state` at <root>/backend_app/core/state.py, dirname is
    # <root>/backend_app/core, so exactly count('.') == 2 levels come off -- NOT
    # count('.') + 1, which overshoots past the root and made every bare-name lookup
    # fail while the sys.path resolution itself was already working.
    base_dir = os.path.dirname(os.path.abspath(filepath))
    for _ in range(module_name.count('.')):
        base_dir = os.path.dirname(base_dir)
    dynamic, unresolved = extract_dynamic_imports(filepath, tree, base_dir)
    imports |= dynamic
    for module, reason in unresolved:
        UNRESOLVED_DYNAMIC_IMPORTS.append((filepath, module, reason))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                _add_with_ancestors(imports, alias.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ''
            level = node.level
            if level > 0:
                # relative import
                parts = module_name.split('.')
                # if level=1, drop the last part (the current module's name).
                base = '.'.join(parts[:-level]) if len(parts) >= level else ''
                if base:
                    mod = f"{base}.{mod}" if mod else base
                else:
                    mod = mod
            if mod:
                _add_with_ancestors(imports, mod)
            for alias in node.names:
                if mod:
                    _add_with_ancestors(imports, f"{mod}.{alias.name}")
    return imports


def resolve_module_to_file(module_name: str, base_dir: str) -> str:
    """Resolve a python module name (e.g., backend_app.core.xxx) to a local file path."""
    parts = module_name.split('.')
    path = os.path.join(base_dir, *parts) + '.py'
    if os.path.isfile(path):
        return os.path.normpath(path)
    init_path = os.path.join(base_dir, *parts, '__init__.py')
    if os.path.isfile(init_path):
        return os.path.normpath(init_path)
    return None


def run_reachability(start_files: List[str], base_dir: str, root_pkg: str) -> Set[str]:
    """Run a BFS from the start files to discover all reachable local files."""
    visited_files = set()
    queue = list(start_files)
    
    while queue:
        current_file = queue.pop(0)
        current_file = os.path.normpath(current_file)
        
        if current_file in visited_files:
            continue
        visited_files.add(current_file)
        
        # Calculate module name for the current file
        rel_path = os.path.relpath(current_file, base_dir)
        mod_name = rel_path.replace('.py', '').replace(os.sep, '.')
        if mod_name.endswith('.__init__'):
            mod_name = mod_name[:-9]

        # Loading ANY module -- whether this file was reached via another
        # file's import statement, or is itself a root entry point run as
        # `python -m a.b.c` -- always loads every ancestor package's
        # __init__.py first (a/__init__.py, then a/b/__init__.py, ...).
        # This must be derived from the file's own path, not just from the
        # import statements found inside OTHER files, so that a package
        # whose only "importer" is an entry point run via `python -m` (e.g.
        # backend_app/workers/__init__.py, reached only because
        # marketplace_expiry_worker.py is a deploy-target root, never via a
        # literal `from backend_app import workers` anywhere) is still
        # correctly marked reachable.
        ancestor_imports: Set[str] = set()
        _add_with_ancestors(ancestor_imports, mod_name)

        imports = extract_imports_from_file(current_file, mod_name)
        imports |= ancestor_imports

        for imp in imports:
            if imp.startswith(root_pkg):
                resolved = resolve_module_to_file(imp, base_dir)
                if resolved and resolved not in visited_files and resolved not in queue:
                    queue.append(resolved)

    return visited_files


def find_all_python_files(target_dir: str) -> Set[str]:
    """Find all python files in the given directory."""
    all_files = set()
    target_path = Path(target_dir)
    for py_file in target_path.glob("**/*.py"):
        all_files.add(os.path.normpath(str(py_file)))
    return all_files


def extract_js_imports_from_file(filepath: str, base_dir: str) -> Set[str]:
    import re
    imports = set()
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception:
        return imports
        
    # Matches: import ... from './path' or require('./path')
    # very naive regex for basic reachability
    matches = re.findall(r'(?:import|from)\s+[\'"]([^\'"]+)[\'"]', content)
    matches += re.findall(r'require\([\'"]([^\'"]+)[\'"]\)', content)
    
    current_dir = os.path.dirname(filepath)
    for m in matches:
        if m.startswith('.'):
            # Resolve relative to current_dir
            resolved = os.path.normpath(os.path.join(current_dir, m))
            # could be .js, .ts, .jsx, .tsx or a directory index
            for ext in ['.js', '.jsx', '.ts', '.tsx', '']:
                test_path = resolved + ext
                if os.path.isfile(test_path):
                    imports.add(test_path)
                    break
                # Directory index
                test_index = os.path.join(resolved, 'index' + ext) if ext else ''
                if test_index and os.path.isfile(test_index):
                    imports.add(test_index)
                    break
    return imports

def run_js_reachability(start_file: str, base_dir: str) -> Set[str]:
    visited_files = set()
    queue = [os.path.normpath(start_file)]
    
    while queue:
        current_file = queue.pop(0)
        if current_file in visited_files:
            continue
        visited_files.add(current_file)
        
        imports = extract_js_imports_from_file(current_file, base_dir)
        for imp in imports:
            if imp not in visited_files and imp not in queue:
                queue.append(imp)
    return visited_files

def find_all_js_files(target_dir: str) -> Set[str]:
    all_files = set()
    target_path = Path(target_dir)
    for ext in ['**/*.js', '**/*.jsx', '**/*.ts', '**/*.tsx']:
        for js_file in target_path.glob(ext):
            if 'node_modules' not in str(js_file) and 'dist' not in str(js_file):
                all_files.add(os.path.normpath(str(js_file)))
    return all_files

def _report_path(path: str) -> str:
    """Render a path for the report with forward slashes on every platform.

    Internally the walk uses os.path.normpath, so on Windows every path is
    backslash-separated. Emitting those raw made this tool's two outputs --
    stdout's `[!] <path>` lines and reports/reachability_audit_results.json
    (gitignored, but uploaded as the nightly-audit-reports CI artifact) --
    platform-dependent, which silently broke the audit's OWN guard tests:
    they compare against forward-slash literals, so on Windows
    `KNOWN_STILL_UNREACHABLE - results` always reported a spurious
    over-widening, while `KNOWN_FALSE_POSITIVES & results` could never match
    and passed vacuously. A guard that cannot fail is worse than one that
    always fails. The graph walk is unchanged; only the rendering is.
    """
    return path.replace(os.sep, '/')


def main():
    print("=== AUTOMATED REACHABILITY AUDIT ===")
    
    base_dir = "."
    target_pkg = "backend_app"
    # Entry points are every module actually invoked as a deployment target,
    # not just backend_app/main.py's FastAPI app. Each one below is backed by
    # a concrete, grep-able deploy/ops reference -- a static import graph walk
    # from main.py alone misses anything only ever launched as its own
    # process (`python -m <module>`, a worker container, a standalone image).
    # Sourced from: terraform/ecs.tf's `worker_definitions` locals block (ECS
    # Fargate task commands), the root Dockerfile*/docker-compose.yml (local
    # dev + the one non-ECS standalone image), and `python -m`/CMD
    # invocations grepped across the repo's deploy/ops tooling.
    #
    # deploy-archive/ is DELIBERATELY NOT a source of entry points here:
    # deploy-archive/README.md states in its own header that none of its
    # k8s/, railway/ or docker-compose/ configs are in active production use
    # ("The sole canonical production deployment target is AWS ECS Fargate
    # via Terraform") -- treating a module as reachable only because an
    # archived, superseded deploy config still names it would mask real dead
    # code (e.g. backend_app/backend/execution_worker.py and
    # portfolio_worker.py, named only in deploy-archive/docker-compose/
    # docker-compose.workers.yml, correctly still flagged below).
    entrypoints = [
        "backend_app/main.py",
        # terraform/ecs.tf worker_definitions.dag + docker-compose.workers.yml (archived, same module)
        "backend_app/backend/dag_worker.py",
        "backend_app/backend/master_executor.py",
        # terraform/ecs.tf worker_definitions.command
        "backend_app/workers/command_worker.py",
        # terraform/ecs.tf worker_definitions.backtest: `python -m backend_app.worker`
        "backend_app/worker.py",
        # terraform/ecs.tf worker_definitions.marketplace_expiry: `python -m
        # backend_app.workers.marketplace_expiry_worker` -- this is HY-02's own
        # confirmed false positive (deployed today; see tests/test_no_new_unhosted_workers.py).
        "backend_app/workers/marketplace_expiry_worker.py",
        # terraform/ecs.tf worker_definitions.training + docker-compose.yml's
        # `training-worker` service: `python -m backend_app.backend.training_worker`
        "backend_app/backend/training_worker.py",
        # Dockerfile.simulator COPYs this exact file into its own standalone
        # image and runs it directly (not imported via the backend_app
        # package at all in that image, but it IS the deployed artifact).
        "backend_app/backend/exchange_simulator.py",
        # backend_app/tee/main.py deleted (RT-23): confirmed-dead code, never deployed.
        "backend_app/mds/main.py"
    ]
    
    # Python Reachability
    valid_entrypoints = [e for e in entrypoints if os.path.isfile(e)]
    if valid_entrypoints:
        reachable_files = run_reachability(valid_entrypoints, base_dir, target_pkg)
        all_files = find_all_python_files(target_pkg)
        unreachable_files = all_files - reachable_files
        true_unreachable = [f for f in unreachable_files if not is_excluded(f)]

        # THE TOOL'S OWN BLIND SPOT, PRINTED RATHER THAN LEFT IMPLICIT.
        #
        # A dynamic import whose target cannot be pinned down means some module may be
        # loaded at runtime without appearing in the graph -- so the dead list below is an
        # upper bound, and this is the list of reasons it might be wrong. Reporting it is
        # the point: `backend_app/backend/alert_engine.py` sat on the dead list across two
        # passes while being imported on every API boot, and nothing in the output hinted
        # that a whole import mechanism was invisible to the scan.
        if UNRESOLVED_DYNAMIC_IMPORTS:
            print(f"\n--- UNRESOLVED DYNAMIC IMPORTS "
                  f"({len(UNRESOLVED_DYNAMIC_IMPORTS)}) ---")
            print("    Modules loaded by name that this scan could NOT resolve. Each one is")
            print("    a way the dead-code list below could be wrong; treat it as an upper")
            print("    bound until these are accounted for.")
            for filepath, module, reason in sorted(UNRESOLVED_DYNAMIC_IMPORTS):
                print(f"    {_report_path(filepath)}: {module} -- {reason}")
        else:
            print("\n--- UNRESOLVED DYNAMIC IMPORTS: none ---")
            print("    Every __import__/import_module/safe_import call with a literal name")
            print("    resolved to a file, so no module is loaded by a mechanism this scan")
            print("    cannot see.")
    else:
        reachable_files = set()
        all_files = set()
        true_unreachable = []

    # JS/TS Reachability
    js_target_pkg = "algo22-terminal/src"
    js_entrypoint = "algo22-terminal/src/main.tsx"
    
    if os.path.isfile(js_entrypoint):
        js_reachable = run_js_reachability(js_entrypoint, js_target_pkg)
        js_all = find_all_js_files(js_target_pkg)
        js_unreachable = js_all - js_reachable
        js_true_unreachable = [f for f in js_unreachable if not is_excluded(f)]
    else:
        js_reachable = set()
        js_all = set()
        js_true_unreachable = []

    total_files = len(all_files) + len(js_all)
    total_reachable = len(reachable_files) + len(js_reachable)
    total_dead = len(true_unreachable) + len(js_true_unreachable)
    
    results = {
        "status": "PASS",
        "total_files": total_files,
        "reachable_files": total_reachable,
        "unreachable_filtered": total_dead,
        "unreachable_files": sorted(
            _report_path(f) for f in true_unreachable + js_true_unreachable
        )
    }
    
    if total_dead > 0:
        results["status"] = "WARN"
        
    print(f"Total files analyzed: {results['total_files']}")
    print(f"Reachable files: {results['reachable_files']}")
    print(f"Dead/Unreachable files: {results['unreachable_filtered']}")
    print(f"Audit Status: {results['status']}")
    
    if total_dead > 0:
        print("\n--- UNREACHABLE FILES (DEAD CODE) ---")
        for uf in results["unreachable_files"]:
            print(f"  [!] {uf}")
            
    os.makedirs("reports", exist_ok=True)
    with open("reports/reachability_audit_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    # Soft-fail initially to prevent blocking CI
    if results["status"] == "FAIL":
        sys.exit(1)
        
    sys.exit(0)

if __name__ == "__main__":
    main()
