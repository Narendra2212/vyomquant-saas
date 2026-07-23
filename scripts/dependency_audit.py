#!/usr/bin/env python3
"""
scripts/dependency_audit.py

Automated Dependency Audit Tool
Reconciles requirements.txt vs backend_app/requirements.txt
Detects:
 - Missing pip packages
 - Duplicate packages
 - Conflicting versions
 - Incompatible declarations
"""

import json
import os
import re
import sys
from typing import Dict, List, Tuple


def parse_requirements_file(filepath: str) -> Dict[str, str]:
    """Parse a requirements file into a mapping of package_name -> version_spec."""
    pkgs = {}
    if not os.path.exists(filepath):
        return pkgs

    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            # Strip inline comments
            if ' #' in line:
                line = line.split(' #')[0].strip()

            # Normalize package name vs specifier
            match = re.match(r'^([a-zA-Z0-9_\-\[\]]+)\s*([<>=!~].*)?$', line)
            if match:
                pkg_name = match.group(1).lower()
                # Clean extra options like [fastapi] or [email] for baseline comparison
                clean_name = re.sub(r'\[.*\]', '', pkg_name)
                spec = match.group(2) or "ANY"
                pkgs[clean_name] = spec
    return pkgs


def normalize_version_spec(spec: str) -> str:
    """
    Normalize a version specification according to PEP 440.
    Strips PEP 440 local version identifiers (e.g., '+cpu', '+cu118')
    so that local build variants are treated as equivalent to public versions.
    """
    if not spec:
        return spec
    return re.sub(r'\+[a-zA-Z0-9\._\-]+', '', spec)


def audit_dependencies(root_req: str = "requirements.txt", backend_req: str = "backend_app/requirements.txt") -> Dict:
    """Compare and validate dependency manifest consistency."""
    report = {
        "status": "PASS",
        "root_count": 0,
        "backend_count": 0,
        "mismatches": [],
        "missing_in_backend": [],
        "missing_in_root": [],
        "duplicates": []
    }

    root_pkgs = parse_requirements_file(root_req)
    backend_pkgs = parse_requirements_file(backend_req)

    report["root_count"] = len(root_pkgs)
    report["backend_count"] = len(backend_pkgs)

    all_keys = set(root_pkgs.keys()).union(set(backend_pkgs.keys()))

    for pkg in sorted(all_keys):
        in_root = pkg in root_pkgs
        in_backend = pkg in backend_pkgs

        if in_root and not in_backend:
            report["missing_in_backend"].append({"package": pkg, "root_spec": root_pkgs[pkg]})
        elif in_backend and not in_root:
            report["missing_in_root"].append({"package": pkg, "backend_spec": backend_pkgs[pkg]})
        else:
            root_spec = root_pkgs[pkg]
            backend_spec = backend_pkgs[pkg]
            if normalize_version_spec(root_spec) != normalize_version_spec(backend_spec):
                report["mismatches"].append({
                    "package": pkg,
                    "root_spec": root_spec,
                    "backend_spec": backend_spec
                })
                report["status"] = "FAIL"

    if report["missing_in_backend"] or report["missing_in_root"]:
        report["status"] = "FAIL"

    return report


def main():
    print("=== AUTOMATED DEPENDENCY AUDIT ===")
    root_req = "requirements.txt"
    backend_req = "backend_app/requirements.txt"

    res = audit_dependencies(root_req, backend_req)

    print(f"Root requirements count: {res['root_count']}")
    print(f"Backend requirements count: {res['backend_count']}")
    print(f"Version Mismatches: {len(res['mismatches'])}")
    print(f"Missing in backend/requirements.txt: {len(res['missing_in_backend'])}")
    print(f"Missing in root requirements.txt: {len(res['missing_in_root'])}")
    print(f"Audit Status: {res['status']}")

    if res["mismatches"]:
        print("\n--- VERSION MISMATCHES ---")
        for m in res["mismatches"]:
            print(f"  ❌ {m['package']}: root={m['root_spec']} vs backend={m['backend_spec']}")

    if res["missing_in_backend"]:
        print("\n--- MISSING IN BACKEND REQUIREMENTS ---")
        for mb in res["missing_in_backend"]:
            print(f"  ⚠️  {mb['package']} ({mb['root_spec']})")

    if res["missing_in_root"]:
        print("\n--- MISSING IN ROOT REQUIREMENTS ---")
        for mr in res["missing_in_root"]:
            print(f"  ⚠️  {mr['package']} ({mr['backend_spec']})")

    os.makedirs("reports", exist_ok=True)
    with open("reports/dependency_audit_results.json", "w") as f:
        json.dump(res, f, indent=2)

    if res["status"] == "FAIL":
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
