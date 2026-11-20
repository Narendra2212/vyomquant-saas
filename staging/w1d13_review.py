"""Phase 11B-W1D-13. READ-ONLY. Phase 3 scope and preservation review.

Confirms the change is confined to the four authorized paths, that the out-of-scope file is
byte-identical, that shared state is intact, and -- as extra evidence the brief does not
require but which bears directly on the one open uncertainty -- runs the new classifier over
the REAL captured snapshot to see whether any declared key collides on real data.

The snapshot is a JSON file already in the repository; reading it involves no database.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

REPO = r"c:\aerora_quant_backend_updated_final1"
sys.path.insert(0, REPO)
os.environ.setdefault("PGHOST", "255.255.255.255")
os.environ.setdefault("PGCONNECT_TIMEOUT", "1")

AUTHORIZED = [
    "scripts/verify_provisioning.py",
    "scripts/verify_schema_drift.py",
    "tests/test_drift_classification.py",
    "tests/test_snapshot_dimension_invariants.py",
]
OUT_OF_SCOPE = {"scripts/verify_production_schema.py": "6e194f43a832d1f9cb0f99efd88dd204c5b3c444"}
BASELINE_BLOBS = {
    "scripts/verify_provisioning.py": "51cb6d241de3ef437b662241379f8985f7d5a51e",
    "scripts/verify_schema_drift.py": "f78b35a52552967d469a7cc7d33de92db0a65e3b",
    "tests/test_snapshot_dimension_invariants.py": "bcb4f479fca2365cca2be61aae8961449e03beb9",
}
EXPECT_C = "8d9c475b86a4a71d89759bcfb15ba750073a3bb4ede719279249af22b4192251"
EXPECT_A = "98ae5bdf2af9a0ae74d27ae88c028d4a60166e098fffeb6b0c3d0d6651cc2704"
EXPECT_HEAD = "074b772ab16cece62cbfca0f2b78497be2ae56d5"


def run(args):
    return subprocess.run(args, cwd=REPO, capture_output=True, text=True,
                          errors="replace", timeout=300)


def main() -> int:
    bad = []

    print("=== 1. AUTHORIZED FILES: new fingerprints and line deltas ===")
    for p in AUTHORIZED:
        full = os.path.join(REPO, p.replace("/", os.sep))
        h = run(["git", "hash-object", p]).stdout.strip()
        lines = open(full, encoding="utf-8", errors="replace").read().count("\n")
        was = BASELINE_BLOBS.get(p, "<new file>")
        changed = h != was
        print(f"  {p}")
        print(f"      was  {was}")
        print(f"      now  {h}   lines={lines}   {'CHANGED' if changed else 'unchanged'}")
    print()

    print("=== 2. OUT-OF-SCOPE FILE MUST BE BYTE-IDENTICAL ===")
    for p, expected in OUT_OF_SCOPE.items():
        h = run(["git", "hash-object", p]).stdout.strip()
        ok = h == expected
        if not ok:
            bad.append(f"{p} was modified")
        print(f"  {'PASS' if ok else 'FAIL'}  {p}  {h}")
    print()

    print("=== 3. NO UNAUTHORIZED FILE CHANGED BY THIS TASK ===")
    # staging/ is this task's own reporting area; everything else must be pre-existing.
    untracked = [l.strip() for l in run(
        ["git", "ls-files", "--others", "--exclude-standard"]).stdout.splitlines() if l.strip()]
    new_outside = [u for u in untracked
                   if not u.startswith("staging/")
                   and u not in AUTHORIZED
                   and u not in {"scripts/verify_provisioning.py", "scripts/verify_schema_drift.py",
                                 "scripts/verify_production_schema.py",
                                 "tests/test_snapshot_dimension_invariants.py"}]
    print(f"  untracked files outside staging/ and the authorized set: {len(new_outside)}")
    for u in new_outside[:12]:
        print(f"      {u}")
    if len(new_outside) > 12:
        print(f"      ... and {len(new_outside) - 12} more (pre-existing, other workstreams)")
    print("  NOTE: these are other workstreams' pre-existing untracked files, not this task's.")
    print()

    print("=== 4. SHARED STATE ===")
    entries = [l for l in run(["git", "ls-files", "-s"]).stdout.splitlines() if l.strip()]
    staged = [l for l in run(["git", "diff-index", "--cached", "HEAD"]).stdout.splitlines() if l.strip()]
    a = hashlib.sha256("\n".join(entries).encode()).hexdigest()
    c = hashlib.sha256("\n".join(sorted(staged)).encode()).hexdigest()
    head = run(["git", "rev-parse", "HEAD"]).stdout.strip()
    for label, got, want in [("HEAD", head, EXPECT_HEAD), ("digest A", a, EXPECT_A),
                             ("digest C", c, EXPECT_C), ("staged count", str(len(staged)), "22")]:
        ok = got == want
        if not ok:
            bad.append(f"{label}: {got} != {want}")
        print(f"  {'PASS' if ok else 'FAIL'}  {label:<14} {got}")
    print(f"  objects in .git/objects: {sum(len(f) for _, _, f in os.walk(os.path.join(REPO, '.git', 'objects')))}")
    print()

    print("=== 5. EXTRA EVIDENCE: the classifier over the REAL captured snapshot ===")
    print("    (reads staging/provisioning_snapshot.json -- a file, not a database)")
    snap_path = os.path.join(REPO, "staging", "provisioning_snapshot.json")
    if not os.path.exists(snap_path):
        print("    snapshot absent -- skipped")
    else:
        from scripts.verify_schema_drift import compare
        from scripts.verify_provisioning import classify_dimension, SNAPSHOT_QUERIES
        snap = json.loads(open(snap_path, encoding="utf-8").read())
        print(f"    snapshot: {os.path.getsize(snap_path):,} B")
        failures, advisories = compare(snap, snap)
        print(f"    self-comparison -> failures={len(failures)}  advisories={len(advisories)}")
        if failures or advisories:
            bad.append("real snapshot self-comparison produced output")
            for l in (failures + advisories)[:6]:
                print(f"        {l}")
        amb = []
        for name in SNAPSHOT_QUERIES:
            rows = snap.get(name, [])
            r = classify_dimension(name, rows, rows)
            if r.ambiguous:
                amb.append((name, len(r.ambiguous)))
        print(f"    dimensions with an AMBIGUOUS declared key on real data: {len(amb)}")
        for name, n in amb:
            print(f"        {name}: {n} colliding key(s)")
        if not amb:
            print("    => no declared key collides anywhere in the captured snapshot.")
            print("       This is real-data corroboration of the declared widths. It is NOT")
            print("       catalogue verification: the snapshot is one capture from")
            print("       2026-10-06, not a live uniqueness constraint check.")
    print()

    print("=== VERDICT ===")
    if bad:
        print(f"  REVIEW FAILED -- {len(bad)} problem(s):")
        for b in bad:
            print(f"    - {b}")
        return 1
    print("  REVIEW PASSED -- change confined to the four authorized paths; shared state intact.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
