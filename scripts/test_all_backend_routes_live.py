import os
import sys
import inspect
import json
import traceback

def audit_router_files_for_syntax_and_imports():
    routers_dir = "backend_app/routers"
    issues = []
    
    for f in os.listdir(routers_dir):
        if f.endswith(".py") and not f.startswith("__"):
            file_path = os.path.join(routers_dir, f)
            with open(file_path, "r", encoding="utf-8") as src:
                lines = src.readlines()

            # Check 1: Shadowed inspect imports inside functions
            has_global_inspect = any("import inspect" in l for l in lines[:50])
            for i, line in enumerate(lines):
                if re_match := re.search(r'^\s+import\s+inspect\b', line):
                    issues.append({
                        "file": file_path,
                        "line": i + 1,
                        "type": "LOCAL_INSPECT_SHADOW",
                        "description": f"Local 'import inspect' inside function shadows module scope and can cause UnboundLocalError: {line.strip()}"
                    })

                # Check 2: Undefined / misspelled response models or table names
                if "supabase.table(" in line:
                    tbl_match = re.search(r'supabase\.table\(["\']([^"\']+)["\']\)', line)
                    if tbl_match:
                        tbl_name = tbl_match.group(1)
                        # We will cross check against known public tables
                        
    return issues

import re

if __name__ == '__main__':
    issues = audit_router_files_for_syntax_and_imports()
    print(f"Found {len(issues)} code-level router defects:")
    for iss in issues:
        print(f"  [{iss['type']}] {iss['file']}:{iss['line']} - {iss['description']}")

    with open('reports/router_code_defects.json', 'w', encoding='utf-8') as f:
        json.dump(issues, f, indent=2)
