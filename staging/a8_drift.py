"""Reports drift between the working tree and each D5' branch for the manifest files.

Phase 11B-A8: the working tree moved after the A7 commit was pushed (another workstream's
A9->A12 work), so the fixes for the CI failures exist locally and on no branch. This
quantifies that per file.

READ-ONLY. Usage: python staging/a8_drift.py
"""

from __future__ import annotations

import hashlib
import os
import subprocess

FILES = [
    ".github/workflows/01-pr-check.yml",
    "tests/real_redis_gate.py",
    "tests/test_quota01_metered_reservation_is_hard.py",
    "tests/test_quota01_exchange_slot_is_atomic.py",
    "tests/test_webhook_duplicate_delivery_real_redis.py",
    "backend_app/core/subscription_dependencies.py",
]
REFS = [
    ("origin/main", "main"),
    ("a624ce89", "A7 PR#8"),
    ("origin/ci/d5-prime-real-redis", "PR#7"),
    ("origin/ci/d5-real-redis-quota-evidence", "PR#6"),
]

MARKERS = {
    "_app_cache_url": "webhook keyspace fix",
    "def exchange_connection_slot_guard": "slot guard API",
    "def _slot_lock_key": "slot lock key API",
}


def blob(ref: str, path: str) -> bytes | None:
    p = subprocess.run(["git", "cat-file", "-p", f"{ref}:{path}"],
                       capture_output=True)
    return p.stdout if p.returncode == 0 else None


def digest(data: bytes | None) -> str:
    return "absent" if data is None else hashlib.sha256(data).hexdigest()[:12]


def main() -> int:
    print("=== SIZE / DIGEST PER REF (working tree vs branches) ===\n")
    header = f"{'file':<52} {'WORKTREE':>18}"
    for _, label in REFS:
        header += f" {label:>14}"
    print(header)
    print("-" * len(header))

    for path in FILES:
        try:
            with open(path, "rb") as handle:
                wt = handle.read()
        except FileNotFoundError:
            wt = None
        row = f"{path:<52} {str(len(wt) if wt is not None else 'absent'):>18}"
        for ref, _ in REFS:
            data = blob(ref, path)
            row += f" {str(len(data) if data is not None else 'absent'):>14}"
        print(row)
    print()

    print("=== MARKER PRESENCE ===\n")
    for marker, label in MARKERS.items():
        print(f"  {label}  ({marker!r})")
        for path in FILES:
            try:
                wt_has = marker in open(path, encoding="utf-8", errors="replace").read()
            except FileNotFoundError:
                continue
            if not wt_has and not any(
                (blob(ref, path) or b"") and marker.encode() in (blob(ref, path) or b"")
                for ref, _ in REFS
            ):
                continue
            cells = []
            for ref, label2 in REFS:
                data = blob(ref, path) or b""
                cells.append(f"{label2}={'Y' if marker.encode() in data else 'n'}")
            print(f"      {path}")
            print(f"          WORKTREE={'Y' if wt_has else 'n'}  " + "  ".join(cells))
        print()

    print("=== CONCLUSION ===")
    wt_wh = open(FILES[4], encoding="utf-8", errors="replace").read()
    wt_impl = open(FILES[5], encoding="utf-8", errors="replace").read()
    fix_local = "_app_cache_url" in wt_wh
    guard_local = "def exchange_connection_slot_guard" in wt_impl
    on_any_branch = any(
        b"_app_cache_url" in (blob(ref, FILES[4]) or b"")
        or b"def exchange_connection_slot_guard" in (blob(ref, FILES[5]) or b"")
        for ref, _ in REFS
    )
    print(f"  webhook keyspace fix present in working tree : {fix_local}")
    print(f"  slot guard API present in working tree       : {guard_local}")
    print(f"  either present on ANY branch                 : {on_any_branch}")
    if (fix_local or guard_local) and not on_any_branch:
        print("\n  => The corrections for the CI failures exist ONLY in the uncommitted")
        print("     working tree. No branch carries them, so all three open PRs fail the")
        print("     same way.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
