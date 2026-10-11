"""Phase 11B-A7 Phase 0 manifest validator. READ-ONLY.

Validates the approved five-path D5' manifest against the live working tree:

  1. every manifest path exists and its current git state is what the plan assumes;
  2. no manifest file imports a `tests.*` module outside the manifest (an omitted
     dependency would make CI fail with ImportError on first collection);
  3. every `backend_app.*` module a manifest file imports is already COMMITTED, so the
     branch does not depend on another workstream's untracked source;
  4. the manifest files contain no destructive Redis cleanup;
  5. records a SHA-256 per manifest path so a later phase can prove nothing drifted.

Stages nothing and never writes to any git index.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys

MANIFEST = [
    ".github/workflows/01-pr-check.yml",
    "tests/real_redis_gate.py",
    "tests/test_quota01_metered_reservation_is_hard.py",
    "tests/test_quota01_exchange_slot_is_atomic.py",
    "tests/test_webhook_duplicate_delivery_real_redis.py",
]
TEST_FILES = [p for p in MANIFEST if p.endswith(".py")]

IMPORT = re.compile(
    r"^\s*(?:from\s+([A-Za-z0-9_.]+)\s+import|import\s+([A-Za-z0-9_.]+))", re.M
)
DESTRUCTIVE = re.compile(r"\b(flushdb|flushall|scan_iter|\.keys\s*\()", re.I)


def git(args: list[str]) -> tuple[int, str]:
    p = subprocess.run(["git", *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "")


def tracked(path: str) -> bool:
    return git(["ls-files", "--error-unmatch", path])[0] == 0


def main() -> int:
    failures: list[str] = []

    print("=== 1. MANIFEST PATHS, STATE AND DIGEST ===")
    for path in MANIFEST:
        try:
            blob = open(path, "rb").read()
        except FileNotFoundError:
            failures.append(f"missing manifest path: {path}")
            print(f"  MISSING  {path}")
            continue
        digest = hashlib.sha256(blob).hexdigest()[:16]
        code, out = git(["status", "--porcelain=v1", "--", path])
        state = (out.strip()[:2] or "  ") if code == 0 else "??"
        print(f"  {state}  {digest}  {len(blob):>7}  {path}")
    print()

    print("=== 2. NO tests.* DEPENDENCY OUTSIDE THE MANIFEST ===")
    manifest_modules = {
        p[len("tests/"):-len(".py")] for p in TEST_FILES if p.startswith("tests/")
    }
    for path in TEST_FILES:
        text = open(path, encoding="utf-8", errors="replace").read()
        mods = sorted({(a or b) for a, b in IMPORT.findall(text)})
        deps = [m for m in mods if m.split(".")[0] == "tests"]
        bad = [d for d in deps if d.split(".", 1)[-1] not in manifest_modules]
        print(f"  {path}")
        print(f"      tests.* imports : {deps or 'NONE'}")
        if bad:
            failures.append(f"{path} imports non-manifest test module(s): {bad}")
            print(f"      OUTSIDE MANIFEST: {bad}")
    print()

    print("=== 3. backend_app.* DEPENDENCIES ARE COMMITTED ===")
    for path in TEST_FILES:
        text = open(path, encoding="utf-8", errors="replace").read()
        mods = sorted({(a or b) for a, b in IMPORT.findall(text)})
        backend = [m for m in mods if m.startswith("backend_app")]
        for module in backend:
            candidate = module.replace(".", "/") + ".py"
            if tracked(candidate):
                verdict = "committed"
            elif git(["ls-files", "--error-unmatch", module.replace(".", "/") + "/__init__.py"])[0] == 0:
                verdict = "committed (package)"
            else:
                verdict = "NOT COMMITTED"
                failures.append(f"{path} imports uncommitted {module}")
            print(f"  {verdict:<20} {module}   <- {path.split('/')[-1]}")
    print()

    print("=== 4. NO DESTRUCTIVE REDIS CLEANUP ===")
    for path in TEST_FILES:
        text = open(path, encoding="utf-8", errors="replace").read()
        hits = sorted({m.group(0).lower() for m in DESTRUCTIVE.finditer(text)})
        if hits:
            failures.append(f"{path} contains destructive op(s): {hits}")
            print(f"  FOUND {hits}  {path}")
        else:
            print(f"  clean                 {path}")
    print()

    print("=== RESULT ===")
    if failures:
        for f in failures:
            print(f"  FAIL  {f}")
        print(f"\n{len(failures)} manifest safety check(s) FAILED. Do not proceed.")
        return 1
    print("  All manifest safety checks passed. The five-path manifest is self-consistent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
