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


def extract_imports_from_file(filepath: str, module_name: str) -> Set[str]:
    """Extract all absolute and resolved-relative imports from a file."""
    imports = set()
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            tree = ast.parse(f.read(), filename=filepath)
    except Exception:
        return imports

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
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
                imports.add(mod)
            for alias in node.names:
                imports.add(f"{mod}.{alias.name}")
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
            
        imports = extract_imports_from_file(current_file, mod_name)
        
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

def main():
    print("=== AUTOMATED REACHABILITY AUDIT ===")
    
    base_dir = "."
    target_pkg = "backend_app"
    entrypoints = [
        "backend_app/main.py",
        "backend_app/backend/dag_worker.py",
        "backend_app/backend/master_executor.py",
        "backend_app/workers/command_worker.py",
        "backend_app/tee/main.py",
        "backend_app/mds/main.py"
    ]
    
    # Python Reachability
    valid_entrypoints = [e for e in entrypoints if os.path.isfile(e)]
    if valid_entrypoints:
        reachable_files = run_reachability(valid_entrypoints, base_dir, target_pkg)
        all_files = find_all_python_files(target_pkg)
        unreachable_files = all_files - reachable_files
        true_unreachable = [f for f in unreachable_files if not is_excluded(f)]
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
        "unreachable_files": sorted(true_unreachable + js_true_unreachable)
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
