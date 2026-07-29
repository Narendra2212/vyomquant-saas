#!/usr/bin/env python3
"""
scripts/size_audit.py

Automated AST Size Audit Tool
Detects:
 - Files exceeding line-count thresholds (prevents monolith files)
 - Functions exceeding line-count thresholds (prevents monolith functions)
"""

import ast
import json
import os
import sys
from pathlib import Path
from typing import Dict, List

# Thresholds calibrated to current post-cleanup codebase
FILE_LINE_THRESHOLD = 3000
FUNCTION_LINE_THRESHOLD = 450


def audit_size(target_dir: str) -> Dict:
    results = {
        "status": "PASS",
        "total_files_scanned": 0,
        "total_functions_scanned": 0,
        "oversized_files": [],
        "oversized_functions": []
    }

    target_path = Path(target_dir)
    if not target_path.exists():
        results["status"] = "FAIL"
        return results

    # Only tracking Python for AST-based function analysis,
    # but we can do simple line count for JS/TS as well.
    extensions = {'.py', '.js', '.ts', '.jsx', '.tsx'}

    for root, dirs, files in os.walk(target_dir):
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('__pycache__', 'node_modules', 'venv', 'env')]
        for f in files:
            ext = os.path.splitext(f)[1]
            if ext in extensions:
                filepath = os.path.join(root, f)
                results["total_files_scanned"] += 1

                try:
                    with open(filepath, 'r', encoding='utf-8') as file:
                        lines = file.readlines()
                        num_lines = len(lines)
                        
                        if num_lines > FILE_LINE_THRESHOLD:
                            results["oversized_files"].append({
                                "file": filepath,
                                "lines": num_lines
                            })
                            results["status"] = "WARN"

                        if ext == '.py':
                            content = "".join(lines)
                            try:
                                tree = ast.parse(content, filename=filepath)
                                for node in ast.walk(tree):
                                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                        results["total_functions_scanned"] += 1
                                        length = getattr(node, 'end_lineno', node.lineno) - node.lineno + 1
                                        if length > FUNCTION_LINE_THRESHOLD:
                                            results["oversized_functions"].append({
                                                "file": filepath,
                                                "function": node.name,
                                                "lines": length
                                            })
                                            results["status"] = "WARN"
                            except SyntaxError:
                                pass
                except Exception:
                    pass

    return results


def main():
    target_dirs = ["backend_app", "algo22-terminal"]
    print("=== AUTOMATED SIZE AUDIT ===")
    
    all_results = {
        "status": "PASS",
        "total_files_scanned": 0,
        "total_functions_scanned": 0,
        "oversized_files": [],
        "oversized_functions": []
    }

    for d in target_dirs:
        res = audit_size(d)
        all_results["total_files_scanned"] += res["total_files_scanned"]
        all_results["total_functions_scanned"] += res["total_functions_scanned"]
        all_results["oversized_files"].extend(res["oversized_files"])
        all_results["oversized_functions"].extend(res["oversized_functions"])
        if res["status"] == "WARN":
            all_results["status"] = "WARN"
        elif res["status"] == "FAIL" and all_results["status"] == "PASS":
            all_results["status"] = "FAIL"

    print(f"Total Files Scanned: {all_results['total_files_scanned']}")
    print(f"Total Functions Scanned (Python): {all_results['total_functions_scanned']}")
    print(f"Oversized Files (> {FILE_LINE_THRESHOLD} lines): {len(all_results['oversized_files'])}")
    print(f"Oversized Functions (> {FUNCTION_LINE_THRESHOLD} lines): {len(all_results['oversized_functions'])}")
    print(f"Audit Status: {all_results['status']}")

    if all_results["oversized_files"]:
        print("\n--- OVERSIZED FILES ---")
        for file_info in sorted(all_results["oversized_files"], key=lambda x: x["lines"], reverse=True):
            print(f"  [!] {file_info['file']}: {file_info['lines']} lines")

    if all_results["oversized_functions"]:
        print("\n--- OVERSIZED FUNCTIONS ---")
        for func_info in sorted(all_results["oversized_functions"], key=lambda x: x["lines"], reverse=True):
            print(f"  [!] {func_info['file']} - {func_info['function']}: {func_info['lines']} lines")

    os.makedirs("reports", exist_ok=True)
    with open("reports/size_audit_results.json", "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2)

    # Soft-fail initially as requested
    if all_results["status"] == "FAIL":
        sys.exit(1)
    
    # WARN exits 0 to avoid breaking builds
    sys.exit(0)


if __name__ == "__main__":
    main()
