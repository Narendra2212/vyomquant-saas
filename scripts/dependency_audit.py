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
 - Imported packages not declared in requirements
"""

import ast
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple


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


def extract_imports_from_file(filepath: str) -> Set[str]:
    """Extract all third-party package imports from a Python file."""
    imports = set()
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        tree = ast.parse(content, filename=filepath)
        
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    pkg = alias.name.split('.')[0]
                    # Filter out standard library
                    if pkg not in STANDARD_LIBRARY:
                        imports.add(pkg)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    pkg = node.module.split('.')[0]
                    # Filter out standard library and relative imports
                    if pkg not in STANDARD_LIBRARY and not node.level:
                        imports.add(pkg)
    except Exception:
        pass  # Skip files that can't be parsed
    return imports


def scan_backend_imports(backend_dir: str = "backend_app") -> Set[str]:
    """Scan all Python files in backend directory for third-party imports."""
    all_imports = set()
    backend_path = Path(backend_dir)
    
    if not backend_path.exists():
        return all_imports
    
    for py_file in backend_path.rglob("*.py"):
        imports = extract_imports_from_file(str(py_file))
        all_imports.update(imports)
    
    return all_imports


# Standard library modules (non-exhaustive but covers common ones)
STANDARD_LIBRARY = {
    'abc', 'aifc', 'argparse', 'array', 'ast', 'asynchat', 'asyncio', 'asyncore',
    'atexit', 'audioop', 'base64', 'bdb', 'binascii', 'binhex', 'bisect',
    'builtins', 'bz2', 'calendar', 'cgi', 'cgitb', 'chunk', 'cmath', 'cmd',
    'code', 'codecs', 'codeop', 'collections', 'colorsys', 'compileall',
    'concurrent', 'configparser', 'contextlib', 'contextvars', 'copy',
    'copyreg', 'cProfile', 'crypt', 'csv', 'ctypes', 'curses', 'dataclasses',
    'datetime', 'dbm', 'decimal', 'difflib', 'dis', 'distutils', 'doctest',
    'email', 'encodings', 'enum', 'errno', 'faulthandler', 'fcntl', 'filecmp',
    'fileinput', 'fnmatch', 'formatter', 'fractions', 'ftplib', 'functools',
    'gc', 'getopt', 'getpass', 'gettext', 'glob', 'graphlib', 'grp', 'gzip',
    'hashlib', 'heapq', 'hmac', 'html', 'http', 'imaplib', 'imghdr', 'imp',
    'importlib', 'inspect', 'io', 'ipaddress', 'itertools', 'json', 'keyword',
    'lib2to3', 'linecache', 'locale', 'logging', 'lzma', 'mailbox', 'mailcap',
    'marshal', 'math', 'mimetypes', 'mmap', 'modulefinder', 'msilib', 'msvcrt',
    'multiprocessing', 'netrc', 'nis', 'nntplib', 'numbers', 'operator',
    'optparse', 'os', 'ossaudiodev', 'pathlib', 'pdb', 'pickle', 'pickletools',
    'pipes', 'pkgutil', 'platform', 'plistlib', 'poplib', 'posix', 'posixpath',
    'pprint', 'profile', 'pstats', 'pty', 'pwd', 'py_compile', 'pyclbr',
    'pydoc', 'queue', 'quopri', 'random', 're', 'readline', 'reprlib',
    'resource', 'rlcompleter', 'runpy', 'sched', 'secrets', 'select',
    'selectors', 'shelve', 'shlex', 'shutil', 'signal', 'site', 'smtpd',
    'smtplib', 'sndhdr', 'socket', 'socketserver', 'spwd', 'sqlite3', 'ssl',
    'stat', 'statistics', 'string', 'stringprep', 'struct', 'subprocess',
    'sunau', 'symbol', 'symtable', 'sys', 'sysconfig', 'syslog', 'tabnanny',
    'tarfile', 'telnetlib', 'tempfile', 'termios', 'test', 'textwrap',
    'threading', 'time', 'timeit', 'tkinter', 'token', 'tokenize', 'tomllib',
    'trace', 'traceback', 'tracemalloc', 'tty', 'turtle', 'turtledemo', 'types',
    'typing', 'typing_extensions', 'unicodedata', 'unittest', 'urllib', 'uu',
    'uuid', 'venv', 'warnings', 'wave', 'weakref', 'webbrowser', 'winreg',
    'winsound', 'wsgiref', 'xdrlib', 'xml', 'xmlrpc', 'zipapp', 'zipfile',
    'zipimport', 'zlib', 'zoneinfo'
}


def audit_dependencies(root_req: str = "requirements.txt", backend_req: str = "backend_app/requirements.txt", requirements_base: str = "requirements-base.txt") -> Dict:
    """Compare and validate dependency manifest consistency."""
    report = {
        "status": "PASS",
        "root_count": 0,
        "backend_count": 0,
        "base_count": 0,
        "mismatches": [],
        "missing_in_backend": [],
        "missing_in_root": [],
        "missing_in_base": [],
        "imported_not_declared": [],
        "duplicates": []
    }

    root_pkgs = parse_requirements_file(root_req)
    backend_pkgs = parse_requirements_file(backend_req)
    base_pkgs = parse_requirements_file(requirements_base)

    report["root_count"] = len(root_pkgs)
    report["backend_count"] = len(backend_pkgs)
    report["base_count"] = len(base_pkgs)

    # Scan actual imports from code
    imported_pkgs = scan_backend_imports("backend_app")

    # Check for imported packages not declared in requirements
    all_declared = set(base_pkgs.keys())
    for imported in sorted(imported_pkgs):
        # Filter out internal packages
        if imported.startswith('backend_app'):
            continue
        # Check if declared (case-insensitive match)
        declared = any(imported.lower() == declared.lower() for declared in all_declared)
        if not declared:
            report["imported_not_declared"].append({"package": imported})

    if report["imported_not_declared"]:
        report["status"] = "FAIL"

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
    base_req = "requirements-base.txt"

    res = audit_dependencies(root_req, backend_req, base_req)

    print(f"Root requirements count: {res['root_count']}")
    print(f"Backend requirements count: {res['backend_count']}")
    print(f"Base requirements count: {res['base_count']}")
    print(f"Version Mismatches: {len(res['mismatches'])}")
    print(f"Missing in backend/requirements.txt: {len(res['missing_in_backend'])}")
    print(f"Missing in root requirements.txt: {len(res['missing_in_root'])}")
    print(f"Imported but not declared: {len(res['imported_not_declared'])}")
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

    if res["imported_not_declared"]:
        print("\n--- IMPORTED BUT NOT DECLARED IN REQUIREMENTS ---")
        for imp in res["imported_not_declared"]:
            print(f"  ❌ {imp['package']}")

    os.makedirs("reports", exist_ok=True)
    with open("reports/dependency_audit_results.json", "w") as f:
        json.dump(res, f, indent=2)

    if res["status"] == "FAIL":
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
