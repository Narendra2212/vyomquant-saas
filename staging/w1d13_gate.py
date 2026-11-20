"""Phase 11B-W1D-13. READ-ONLY PRE-EDIT GATE. Abort on any mismatch.

Every check must pass before the first edit. Exit code is non-zero on any mismatch so the
gate cannot be mistaken for a pass.
"""

from __future__ import annotations

import hashlib
import io
import os
import subprocess
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

REPO = r"c:\aerora_quant_backend_updated_final1"

EXPECT_HEAD = "074b772ab16cece62cbfca0f2b78497be2ae56d5"
EXPECT_BRANCH = "main"
EXPECT_STAGED = 22
EXPECT_C = "8d9c475b86a4a71d89759bcfb15ba750073a3bb4ede719279249af22b4192251"
EXPECT_A = "98ae5bdf2af9a0ae74d27ae88c028d4a60166e098fffeb6b0c3d0d6651cc2704"

EXPECT_BLOBS = {
    "scripts/verify_provisioning.py": "51cb6d241de3ef437b662241379f8985f7d5a51e",
    "scripts/verify_schema_drift.py": "f78b35a52552967d469a7cc7d33de92db0a65e3b",
    "tests/test_snapshot_dimension_invariants.py": "bcb4f479fca2365cca2be61aae8961449e03beb9",
}
MUST_BE_ABSENT = "tests/test_drift_classification.py"
OUT_OF_SCOPE = {"scripts/verify_production_schema.py": "6e194f43a832d1f9cb0f99efd88dd204c5b3c444"}


def run(args):
    return subprocess.run(args, cwd=REPO, capture_output=True, text=True,
                          errors="replace", timeout=240)


def main() -> int:
    failures = []

    def check(label, actual, expected):
        ok = actual == expected
        if not ok:
            failures.append(f"{label}: expected {expected!r}, got {actual!r}")
        print(f"  {'PASS' if ok else 'FAIL'}  {label:<52} {actual}")

    print("=== PRE-EDIT GATE ===")
    check("HEAD", run(["git", "rev-parse", "HEAD"]).stdout.strip(), EXPECT_HEAD)
    check("branch", run(["git", "rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip(), EXPECT_BRANCH)
    check("divergence origin/main...HEAD",
          run(["git", "rev-list", "--left-right", "--count", "origin/main...HEAD"]).stdout.strip(),
          "0\t0")

    entries = [l for l in run(["git", "ls-files", "-s"]).stdout.splitlines() if l.strip()]
    staged = [l for l in run(["git", "diff-index", "--cached", "HEAD"]).stdout.splitlines() if l.strip()]
    check("staged count", len(staged), EXPECT_STAGED)
    check("digest A", hashlib.sha256("\n".join(entries).encode()).hexdigest(), EXPECT_A)
    check("digest C", hashlib.sha256("\n".join(sorted(staged)).encode()).hexdigest(), EXPECT_C)
    print()

    print("=== AUTHORIZED EXISTING FILES ===")
    for path, expected in EXPECT_BLOBS.items():
        check(f"blob {path}", run(["git", "hash-object", path]).stdout.strip(), expected)
    print()

    print("=== NEW FILE MUST BE ABSENT ===")
    absent = not os.path.exists(os.path.join(REPO, MUST_BE_ABSENT.replace("/", os.sep)))
    if not absent:
        failures.append(f"{MUST_BE_ABSENT} already exists")
    print(f"  {'PASS' if absent else 'FAIL'}  {MUST_BE_ABSENT} absent = {absent}")
    print()

    print("=== OUT-OF-SCOPE FILE (recorded, must stay untouched) ===")
    for path, expected in OUT_OF_SCOPE.items():
        check(f"blob {path}", run(["git", "hash-object", path]).stdout.strip(), expected)
    print()

    print("=== CONCURRENT EDITS TO ANY AUTHORIZED PATH ===")
    allp = list(EXPECT_BLOBS) + [MUST_BE_ABSENT] + list(OUT_OF_SCOPE)
    unstaged = {l.strip() for l in run(["git", "diff", "--name-only"]).stdout.splitlines()}
    cached = {l.strip() for l in run(["git", "diff", "--cached", "--name-only"]).stdout.splitlines()}
    for p in allp:
        inu, inc = p in unstaged, p in cached
        bad = inu or inc
        if bad:
            failures.append(f"{p} has concurrent modifications (unstaged={inu}, staged={inc})")
        print(f"  {'PASS' if not bad else 'FAIL'}  {p:<48} unstaged={inu} staged={inc}")
    print()

    print("=== VERDICT ===")
    if failures:
        print(f"  GATE FAILED -- {len(failures)} mismatch(es). DO NOT EDIT.")
        for f in failures:
            print(f"    - {f}")
        return 1
    print("  GATE PASSED -- all checks match the recorded baseline. Editing is authorized.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
