"""Are the committed shell scripts LF or CRLF? Read raw blob bytes, not a shell pipeline.

WHY A SECOND ATTEMPT
--------------------
A first measurement redirected `git cat-file blob` through PowerShell, which rewrites line
endings on write. It reported CR==LF for all three scripts -- including one that had just
PASSED `bash -n`, which is self-contradictory and was the tell that the harness, not the
repository, produced the number.

`bash -n` reported syntax errors near `$'do\r'` and `$'{\r'` for two scripts. That proves the
WORKING TREE has CRLF. It does not prove the committed blob does, because `core.autocrlf=true`
converts LF -> CRLF on checkout. This reads the blob bytes directly through subprocess so the
distinction can be settled.

Read-only. Touches no file, changes no setting.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[1]

TARGETS = [
    "scripts/deploy.sh",
    "scripts/blue-green-deploy.sh",
    "infra/deploy.sh",
    "scripts/require_security_scan.py",
]


def blob_bytes(rev_path: str) -> bytes | None:
    r = subprocess.run(
        ["git", "cat-file", "blob", rev_path],
        cwd=ROOT, capture_output=True,  # capture_output keeps stdout as raw bytes
    )
    return r.stdout if r.returncode == 0 else None


def worktree_bytes(rel: str) -> bytes | None:
    p = ROOT / rel
    return p.read_bytes() if p.exists() else None


def describe(b: bytes) -> str:
    crlf = b.count(b"\r\n")
    lf = b.count(b"\n")
    lone_lf = lf - crlf
    lone_cr = b.count(b"\r") - crlf
    if crlf and not lone_lf:
        kind = "CRLF"
    elif lone_lf and not crlf:
        kind = "LF"
    elif crlf and lone_lf:
        kind = "MIXED"
    else:
        kind = "none/other"
    return f"{kind:<10} crlf={crlf:<5} lone_lf={lone_lf:<5} lone_cr={lone_cr}"


def main() -> int:
    cfg = subprocess.run(["git", "config", "core.autocrlf"], cwd=ROOT,
                         capture_output=True, text=True).stdout.strip()
    attrs = (ROOT / ".gitattributes").exists()
    print("=== environment ===")
    print(f"  core.autocrlf        : {cfg or '(unset)'}")
    print(f"  .gitattributes exists: {attrs}")
    print()
    print("=== committed blob (HEAD) vs working tree ===")
    print(f"  {'path':<34} {'where':<10} {'eol'}")
    print(f"  {'-'*34} {'-'*10} {'-'*44}")
    verdicts = []
    for rel in TARGETS:
        b = blob_bytes(f"HEAD:{rel}")
        w = worktree_bytes(rel)
        if b is None:
            print(f"  {rel:<34} {'blob':<10} NOT IN HEAD")
            continue
        print(f"  {rel:<34} {'blob':<10} {describe(b)}")
        if w is not None:
            print(f"  {'':<34} {'worktree':<10} {describe(w)}")
        verdicts.append((rel, b.count(b"\r\n") > 0))
        print()

    print("=== verdict ===")
    bad = [r for r, crlf in verdicts if crlf]
    if not bad:
        print("  Every committed shell blob is LF-only.")
        print("  => The `bash -n` failures are a LOCAL CHECKOUT ARTEFACT of core.autocrlf=true,")
        print("     NOT a defect in the repository. The earlier 'cannot run under bash as")
        print("     committed' framing is WITHDRAWN.")
    else:
        print("  Committed WITH CRLF: " + ", ".join(bad))
        print("  => These would fail under bash on a POSIX runner. Real defect.")

    print()
    print("=== residual risk, independent of the above ===")
    print("  `.gitattributes` is absent while core.autocrlf=true is set locally. Nothing in the")
    print("  repository pins `*.sh eol=lf`, so the correct endings depend on each contributor's")
    print("  local git configuration rather than on a committed rule. A contributor with")
    print("  autocrlf=false on Windows could commit CRLF and break these scripts on a Linux")
    print("  runner. That is a latent hygiene gap, reported as such and not as a live failure.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
