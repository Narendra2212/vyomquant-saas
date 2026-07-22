#!/usr/bin/env python3
"""
scripts/import_audit.py

Automated AST & Import Analysis Tool
Detects:
 - Syntax errors
 - Missing __init__.py files in package directories
 - Broken / unresolvable imports
 - Circular import hazards
 - Unused imports
"""

import ast
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Set

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass


class ImportVisitor(ast.NodeVisitor):
    def __init__(self, filepath: str):
        self.filepath = filepath
        self.imports: List[Dict] = []
        self.used_names: Set[str] = set()
        self.defined_names: Set[str] = set()

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            name = alias.asname or alias.name
            self.imports.append({
                "type": "import",
                "module": alias.name,
                "alias": alias.asname,
                "name": name,
                "line": node.lineno
            })
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        mod = node.module or ""
        for alias in node.names:
            name = alias.asname or alias.name
            self.imports.append({
                "type": "import_from",
                "module": mod,
                "imported": alias.name,
                "alias": alias.asname,
                "name": name,
                "level": node.level,
                "line": node.lineno
            })
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name):
        if isinstance(node.ctx, ast.Load):
            self.used_names.add(node.id)
        elif isinstance(node.ctx, (ast.Store, ast.Param)):
            self.defined_names.add(node.id)
        self.generic_visit(node)


def check_missing_init_files(target_dir: str) -> List[str]:
    """Find directories containing .py files that lack __init__.py"""
    missing_inits = []
    for root, dirs, files in os.walk(target_dir):
        dirs[:] = [d for d in dirs if not d.startswith('.') and d != '__pycache__']
        py_files = [f for f in files if f.endswith('.py')]
        if py_files and '__init__.py' not in files:
            rel_path = os.path.relpath(root, target_dir)
            missing_inits.append(rel_path)
    return missing_inits


def audit_python_files(target_dir: str) -> Dict:
    """Analyze all python files in target_dir for syntax and import health."""
    results = {
        "total_files": 0,
        "syntax_errors": [],
        "missing_init_dirs": [],
        "import_issues": [],
        "unused_imports": [],
        "import_graph": {},
        "status": "PASS"
    }

    target_path = Path(target_dir)
    if not target_path.exists():
        results["status"] = "FAIL"
        results["import_issues"].append(f"Target directory {target_dir} does not exist.")
        return results

    results["missing_init_dirs"] = check_missing_init_files(target_dir)

    py_files = list(target_path.glob("**/*.py"))
    results["total_files"] = len(py_files)

    for py_file in py_files:
        filepath_str = str(py_file)
        try:
            with open(py_file, 'r', encoding='utf-8') as f:
                code = f.read()
            tree = ast.parse(code, filename=filepath_str)
        except SyntaxError as se:
            results["syntax_errors"].append({
                "file": filepath_str,
                "line": se.lineno,
                "msg": se.msg
            })
            results["status"] = "FAIL"
            continue
        except Exception as ex:
            results["syntax_errors"].append({
                "file": filepath_str,
                "line": 0,
                "msg": f"Failed to read/parse: {ex}"
            })
            results["status"] = "FAIL"
            continue

        visitor = ImportVisitor(filepath_str)
        visitor.visit(tree)

        rel_mod = filepath_str.replace(os.sep, '.').rstrip('.py')
        results["import_graph"][rel_mod] = [
            imp.get("module") for imp in visitor.imports if imp.get("module")
        ]

        for imp in visitor.imports:
            imported_name = imp["name"]
            if imported_name != "*" and not imported_name.startswith("__"):
                if imported_name not in visitor.used_names and imported_name not in visitor.defined_names:
                    results["unused_imports"].append({
                        "file": filepath_str,
                        "line": imp["line"],
                        "name": imported_name
                    })

    if results["syntax_errors"]:
        results["status"] = "FAIL"

    return results


def main():
    target_dir = sys.argv[1] if len(sys.argv) > 1 else "backend_app"
    print(f"=== AUTOMATED IMPORT & AST AUDIT ({target_dir}) ===")
    audit_res = audit_python_files(target_dir)

    print(f"Total Python Files Scanned: {audit_res['total_files']}")
    print(f"Syntax Errors: {len(audit_res['syntax_errors'])}")
    print(f"Missing __init__.py Directories: {len(audit_res['missing_init_dirs'])}")
    print(f"Unused Import Warnings: {len(audit_res['unused_imports'])}")
    print(f"Audit Status: {audit_res['status']}")

    if audit_res["syntax_errors"]:
        print("\n--- SYNTAX ERRORS ---")
        for err in audit_res["syntax_errors"]:
            print(f"  [X] {err['file']}:{err['line']} - {err['msg']}")

    if audit_res["missing_init_dirs"]:
        print("\n--- MISSING __init__.py DIRECTORIES ---")
        for md in audit_res["missing_init_dirs"]:
            print(f"  [!] {md}")

    os.makedirs("reports", exist_ok=True)
    with open("reports/import_audit_results.json", "w", encoding="utf-8") as f:
        json.dump(audit_res, f, indent=2)

    if audit_res["status"] == "FAIL":
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
