"""Phase 34B — census of GitHub Actions `uses:` pins across all workflows.

Read-only. Classifies every `uses:` reference as SHA-pinned, tag-pinned, branch-pinned, or
local, and separates first-party (`actions/`, `github/`) from third-party publishers.

WHY. A tag is a mutable pointer. A publisher — or anyone who compromises one — can move a tag
to different code, and a workflow that references `@v1.2.3` will silently execute it. Pinning
to a full commit SHA removes that mutability. This census quantifies how much of the pipeline
currently depends on tag mutability.
"""
from __future__ import annotations

import collections
import pathlib
import re

USES_RE = re.compile(r"^\s*-?\s*uses:\s*['\"]?([^'\"\s#]+)")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")

FIRST_PARTY = ("actions/", "github/")


def main() -> int:
    rows = []
    for path in sorted(pathlib.Path(".github/workflows").glob("*.y*ml")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            m = USES_RE.match(line)
            if not m:
                continue
            ref = m.group(1)
            if ref.startswith("./") or ref.startswith(".github/"):
                kind = "local"
                action, version = ref, "-"
            elif "@" in ref:
                action, version = ref.rsplit("@", 1)
                kind = "SHA" if SHA_RE.match(version) else "TAG/BRANCH"
            else:
                action, version, kind = ref, "(none)", "UNPINNED"
            rows.append((path.name, lineno, action, version, kind))

    kinds = collections.Counter(r[4] for r in rows)
    print("GitHub Actions `uses:` census over .github/workflows")
    print("  total references: %d" % len(rows))
    for k, c in kinds.most_common():
        print("    %-12s %d" % (k, c))
    print("")

    mutable = [r for r in rows if r[4] in ("TAG/BRANCH", "UNPINNED")]
    third = [r for r in mutable if not r[2].startswith(FIRST_PARTY)]
    print("  mutable refs: %d   of which THIRD-PARTY: %d" % (len(mutable), len(third)))
    print("")

    print("  THIRD-PARTY, mutable (highest supply-chain exposure)")
    print("  %-26s %-6s %-40s %s" % ("workflow", "line", "action", "ref"))
    for wf, ln, action, ver, _ in sorted(third):
        print("  %-26s %-6s %-40s %s" % (wf, ln, action, ver))
    print("")

    by_action = collections.Counter(r[2] for r in third)
    print("  third-party actions by reference count")
    for action, c in by_action.most_common():
        print("    %-44s %d" % (action, c))

    print("")
    print("  first-party, mutable (lower but non-zero exposure)")
    fp = collections.Counter(
        "%s@%s" % (r[2], r[3]) for r in mutable if r[2].startswith(FIRST_PARTY)
    )
    for ref, c in fp.most_common():
        print("    %-44s %d" % (ref, c))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
