"""Phase 11B-A10. READ-ONLY. Fresh rebaseline + destination safety check.

A15 reports that its own A14 run DESTROYED a concurrent workstream's report by whole-file
writing to a path it had not checked. So the destination check comes first, before any
other work, and it checks the A10 path plus every other name this phase might use.

Then: fresh baseline, with the digest METHOD recorded alongside the value. Three different
digests are now circulating for the same idea (A14 `7379218b`, A15 `CF474F1B`, A9
`98ae5bdf`) because each hashes a different serialisation. A value without its method is
useless, so both are printed.
"""

from __future__ import annotations

import hashlib
import os
import subprocess

REPO = r"c:\aerora_quant_backend_updated_final1"
STAGING = os.path.join(REPO, "staging")

DEST = "PHASE11B_A10_D5_APPROVAL_READINESS.md"
NAMED_BY_BRIEF = [
    "PHASE11B_A9_D5_CONSOLIDATION_REPORT.md",
    "PHASE11B_A14_D5_CONSOLIDATION_REPORT.md",
    "PHASE11B_A12_WEBHOOK_KEYSPACE_FIX.md",
    "PHASE11B_A13_D5_REPAIR_REPORT.md",
    "PHASE11B_A9_D5_FAILURE_DIAGNOSTICS.md",
]


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=REPO, capture_output=True, text=True, errors="replace")


def main() -> int:
    print("=== DESTINATION SAFETY (A15 destroyed a report by skipping this) ===")
    dest_path = os.path.join(STAGING, DEST)
    exists = os.path.exists(dest_path)
    print(f"  {DEST}")
    print(f"    exists = {exists}" + (f"  size={os.path.getsize(dest_path)}" if exists else "  -> SAFE TO WRITE"))
    print()

    print("=== REPORTS NAMED BY THE BRIEF ===")
    for n in NAMED_BY_BRIEF:
        p = os.path.join(STAGING, n)
        if os.path.exists(p):
            print(f"  EXISTS   {os.path.getsize(p):>7} B  {n}")
        else:
            print(f"  MISSING  {'':>7}    {n}")
    print()

    print("=== D5-RELEVANT REPORTS ACTUALLY PRESENT (name discipline) ===")
    for n in sorted(os.listdir(STAGING)):
        if not n.startswith("PHASE11B"):
            continue
        low = n.lower()
        if any(k in low for k in ("d5", "redis", "webhook", "quota")):
            print(f"  {os.path.getsize(os.path.join(STAGING, n)):>7} B  {n}")
    print()
    print("  NOTE the reused labels -- these are NOT D5' and must not be cited as such:")
    for n in sorted(os.listdir(STAGING)):
        if "POSTGRES" in n.upper() or n.upper().startswith("PHASE11B_PG"):
            print(f"    {n}")
    print()

    print("=== BASELINE ===")
    for label, args in [
        ("HEAD", ["git", "rev-parse", "HEAD"]),
        ("branch", ["git", "rev-parse", "--abbrev-ref", "HEAD"]),
        ("origin/main", ["git", "rev-parse", "origin/main"]),
        ("divergence L/R", ["git", "rev-list", "--left-right", "--count", "origin/main...HEAD"]),
    ]:
        r = run(args)
        print(f"  {label:<16} {r.stdout.strip()}")

    ls = run(["git", "ls-files", "-s"])
    entries = [l for l in ls.stdout.splitlines() if l.strip()]
    digest_lines = hashlib.sha256("\n".join(entries).encode()).hexdigest()

    # A15-style: hash only the (mode, blob, stage, path) tuples, tab-normalised, sorted.
    tuples = []
    for l in entries:
        meta, _, path = l.partition("\t")
        parts = meta.split()
        if len(parts) >= 3:
            tuples.append(f"{parts[0]} {parts[1]} {parts[2]} {path}")
    digest_tuples = hashlib.sha256("\n".join(sorted(tuples)).encode()).hexdigest()

    di = run(["git", "diff-index", "--cached", "HEAD"])
    staged_rows = [l for l in di.stdout.splitlines() if l.strip()]
    digest_staged = hashlib.sha256("\n".join(sorted(staged_rows)).encode()).hexdigest()

    print(f"  tracked entries  {len(entries)}")
    print(f"  staged paths     {len(staged_rows)}")
    for label, args in [
        ("unstaged tracked", ["git", "diff", "--name-only"]),
        ("untracked", ["git", "ls-files", "--others", "--exclude-standard"]),
    ]:
        r = run(args)
        print(f"  {label:<16} {len([l for l in r.stdout.splitlines() if l.strip()])}")
    r = run(["git", "ls-files", "-u"])
    print(f"  unmerged         {len([l for l in r.stdout.splitlines() if l.strip()])}")
    r = run(["git", "stash", "list"])
    print(f"  stash entries    {len([l for l in r.stdout.splitlines() if l.strip()])}")
    print()

    print("=== DIGESTS, EACH WITH ITS METHOD ===")
    print(f"  A  sha256(newline-joined raw `git ls-files -s` lines, file order)")
    print(f"       = {digest_lines}")
    print(f"  B  sha256(sorted 'mode blob stage path' tuples, whitespace-normalised)")
    print(f"       = {digest_tuples}")
    print(f"  C  sha256(sorted `git diff-index --cached HEAD` rows)  [22 staged paths only]")
    print(f"       = {digest_staged}")
    print()
    print("  Prior phases quoted: A9/A10=A-method 98ae5bdf..., A14=7379218b..., A15=CF474F1B...")
    print("  Different serialisations of the same state. Compare like with like only.")
    print()

    print("=== CONCURRENT WORKSTREAM ARTIFACTS (must be preserved) ===")
    for prefix in ("p11b15_", "p11b14_"):
        hits = sorted(n for n in os.listdir(STAGING) if n.startswith(prefix))
        print(f"  {prefix}*  count={len(hits)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
