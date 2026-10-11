#!/usr/bin/env python3
"""PHASE 38 / PROTOTYPE ONLY -- the second, dormant workflow directory.

`aerora_quant_platform/deployment/.github/workflows/` holds a complete parallel
CI/CD definition. GitHub executes only the repository-root `.github/workflows/`,
so these never run -- yet they define the same pipeline differently, and on
several controls they are stricter than the live one.

Runs the Phase 31 invariant checker against both directories and diffs them.
Reads only.
"""
from __future__ import annotations

import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import phase31_workflow_invariants as C  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent
ROOT_WF = REPO / ".github" / "workflows"
NESTED_WF = REPO / "aerora_quant_platform" / "deployment" / ".github" / "workflows"


def report(label, directory):
    print("")
    print("=" * 94)
    print(label)
    print("   %s" % directory.relative_to(REPO).as_posix())
    print("=" * 94)
    if not directory.is_dir():
        print("  directory absent")
        return []
    print("  workflows: %s" % ", ".join(sorted(p.name for p in directory.glob("*.y*ml"))))
    findings = C.run(directory)
    if not findings:
        print("  all five invariants hold")
    for f in findings:
        print("     %-34s %s :: %s" % (f.invariant, f.workflow, f.where))
    if findings:
        print("  %d violation(s)" % len(findings))
    return findings


def main():
    root = report("A. LIVE pipeline (GitHub executes this)", ROOT_WF)
    nested = report("B. DORMANT pipeline (GitHub never executes this)", NESTED_WF)

    print("")
    print("=" * 94)
    print("C. Invariant-by-invariant difference")
    print("=" * 94)
    rset = {f.invariant.split()[0] for f in root}
    nset = {f.invariant.split()[0] for f in nested}
    print("")
    print("  %-10s %-14s %s" % ("invariant", "live", "dormant"))
    print("  %-10s %-14s %s" % ("-" * 10, "-" * 14, "-" * 14))
    for inv in ("INV-1", "INV-2", "INV-3", "INV-4", "INV-5"):
        print("  %-10s %-14s %s" % (
            inv,
            "VIOLATED" if inv in rset else "holds",
            "VIOLATED" if inv in nset else "holds"))
    only_live = sorted(rset - nset)
    if only_live:
        print("")
        print("  Satisfied by the DORMANT pipeline but violated by the LIVE one: %s"
              % ", ".join(only_live))
    only_nested = sorted(nset - rset)
    if only_nested:
        print("  Violated by the DORMANT pipeline only: %s" % ", ".join(only_nested))

    print("")
    print("=" * 94)
    print("D. Mechanism comparison")
    print("=" * 94)
    checks = [
        ("digest obtained by shelling out to the registry",
         ROOT_WF / "02-build.yml", r"DIGEST=\$\(.*describe-images"),
        ("digest taken from build-push-action's own output",
         NESTED_WF / "ci-cd.yml", r"image_digest:\s*\$\{\{\s*steps\.build\.outputs\.digest"),
        ("build job declares a security dependency",
         NESTED_WF / "ci-cd.yml", r"needs:\s*\[test,\s*security\]"),
        ("build job declares ANY needs (live)",
         ROOT_WF / "02-build.yml", r"^\s*needs:"),
        ("safety exit status swallowed",
         ROOT_WF / "05-security.yml", r"safety check.*\|\|"),
        ("safety exit status preserved",
         NESTED_WF / "ci-cd.yml", r"^\s*safety check\s*$"),
        ("trivy action pinned to a release tag",
         ROOT_WF / "05-security.yml", r"trivy-action@v[\d.]+"),
        ("trivy action tracks a mutable branch",
         NESTED_WF / "ci-cd.yml", r"trivy-action@master"),
        ("deploys using the TAG output",
         NESTED_WF / "ci-cd.yml", r"needs\.build\.outputs\.image_tag"),
        ("deploys using the DIGEST output",
         NESTED_WF / "ci-cd.yml", r"needs\.build\.outputs\.image_digest"),
    ]
    print("")
    for label, path, pat in checks:
        text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        hits = len(re.findall(pat, text, re.M))
        where = path.relative_to(REPO).as_posix()
        print("  %-48s %-34s n=%d" % (label, where, hits))

    print("")
    print("=" * 94)
    print("E. Reading")
    print("=" * 94)
    print("  The dormant pipeline captures the digest from docker/build-push-action's")
    print("  own `steps.build.outputs.digest` and carries it through")
    print("  `needs.build.outputs.image_digest`. That is precisely the mechanism the")
    print("  live pipeline lacks and precisely the shape Option A requires -- a working")
    print("  reference for it already sits in this repository.")
    print("")
    print("  It then deploys with `needs.build.outputs.image_tag`, so it arrives at the")
    print("  same end state by a different route: a correct digest, computed and unused.")
    print("")
    print("  It is stricter than the live pipeline on the build gate and on Safety, and")
    print("  looser on one control the live pipeline deliberately hardened:")
    print("  `trivy-action@master` is a mutable ref, and 05-security.yml pins a release")
    print("  tag with a comment explaining why mutable scanner refs are unsafe.")
    print("")
    print("  Two divergent definitions of one pipeline, one unexecuted, is a drift")
    print("  hazard: nothing in the files says which is authoritative.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
