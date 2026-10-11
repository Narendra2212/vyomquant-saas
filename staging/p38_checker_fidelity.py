#!/usr/bin/env python3
"""
PHASE 38 / PROTOTYPE ONLY -- fidelity audit of staging/phase31_workflow_invariants.py.

Phase 35 established that the checker's own 21-test suite passes. That proves it can
separate SOME compliant fixtures from SOME violating ones. It does not establish that
its detectors cover the variations that matter, nor that it would accept a correct fix.

This probe drives the checker's public check_* functions with synthetic workflow
documents and compares each result against the outcome the invariant's own docstring
requires. It writes nothing except its own stdout and reads no repository workflow.

Exit 0 = probe ran. Defect count is reported in the summary, not in the exit status,
because this audits a prototype rather than gating anything.
"""
from __future__ import annotations

import pathlib
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import phase31_workflow_invariants as C  # noqa: E402

DEFECTS: list[tuple[str, str, str]] = []


def Y(text: str) -> dict:
    return yaml.safe_load(text)


def show(group: str, label: str, findings: list, expect_finding: bool) -> None:
    got = len(findings)
    fired = got > 0
    if fired == expect_finding:
        verdict = "as specified"
    else:
        verdict = ">>> DEFECT <<<"
        kind = "FALSE NEGATIVE" if expect_finding else "FALSE POSITIVE"
        DEFECTS.append((group, label, kind))
    print("  %-62s n=%d  %s" % (label[:62], got, verdict))


def banner(text: str) -> None:
    print("")
    print("=" * 98)
    print(text)
    print("=" * 98)


# --------------------------------------------------------------- INV-5 vs options
banner("A. INV-5 judged against the four candidate digest architectures")
print("  An invariant that rejects a correct remedy is worse than no invariant: it")
print("  tells the owner their fix is a violation.")
print("")

opt_a = {
    "build.yml": Y(
        "on: {workflow_call: {outputs: {image_digest: {value: v}}}}\n"
        "jobs:\n"
        "  b:\n"
        "    outputs: {image_digest: d}\n"
        "    steps: [{id: s, run: 'IMAGE_DIGEST=$(aws ecr describe-images)'}]\n"
    ),
    "deploy.yml": Y(
        "on: {workflow_dispatch: {}}\n"
        "jobs:\n"
        "  d:\n"
        "    needs: [b]\n"
        "    steps: [{run: 'echo ${{ needs.b.outputs.image_digest }}'}]\n"
    ),
}
show("INV-5", "Option A  reusable workflow, cross-workflow needs",
     C.check_digest_is_consumed(opt_a), False)

opt_d = {
    "combined.yml": Y(
        "on: {push: {}}\n"
        "jobs:\n"
        "  build:\n"
        "    outputs: {image_digest: d}\n"
        "    steps: [{id: s, run: 'IMAGE_DIGEST=$(aws ecr describe-images)'}]\n"
        "  deploy:\n"
        "    needs: [build]\n"
        "    steps: [{run: 'kubectl set image a=r@${{ needs.build.outputs.image_digest }}'}]\n"
    ),
}
show("INV-5", "Option D  single workflow, same-workflow needs",
     C.check_digest_is_consumed(opt_d), False)

opt_b = {
    "build.yml": Y(
        "on: {push: {}}\n"
        "jobs:\n"
        "  b:\n"
        "    steps:\n"
        "      - {run: 'IMAGE_DIGEST=$(aws ecr describe-images)'}\n"
        "      - {uses: 'actions/upload-artifact@v4', with: {name: digest}}\n"
    ),
    "deploy.yml": Y(
        "on: {workflow_run: {workflows: [build]}}\n"
        "jobs:\n"
        "  d:\n"
        "    steps:\n"
        "      - {uses: 'actions/download-artifact@v4', with: {name: digest}}\n"
        "      - {run: 'D=$(cat digest.txt); kubectl set image a=r@$D'}\n"
    ),
}
show("INV-5", "Option B  digest carried as a build artifact [see note]",
     C.check_digest_is_consumed(opt_b), False)

opt_c = {
    "build.yml": Y(
        "on: {push: {}}\n"
        "jobs: {b: {steps: [{run: 'IMAGE_DIGEST=$(aws ecr describe-images)'}]}}\n"
    ),
    "deploy.yml": Y(
        "on: {workflow_run: {workflows: [build]}}\n"
        "jobs: {d: {steps: [{run: 'D=$(aws ecr describe-images --query imageDigest);"
        " kubectl set image a=r@$D'}]}}\n"
    ),
}
show("INV-5", "Option C  deploy resolves digest from registry [see note]",
     C.check_digest_is_consumed(opt_c), False)

print("")
print("  NOTE on Options B and C: they report no violation, but NOT because the")
print("  checker judged them compliant. Their fixtures carry the digest in an")
print("  uppercase shell variable (IMAGE_DIGEST=), and the exporter detector is")
print("  case-sensitive, so neither was ever analysed. Demonstrated below.")
print("")
for label, doc in (
    ("IMAGE_DIGEST=abc   uppercase shell var (what 02-build.yml:78 uses)",
     {"b.yml": {"jobs": {"j": {"steps": [{"run": "IMAGE_DIGEST=abc"}]}}}}),
    ("image_digest=abc   lowercase shell var",
     {"b.yml": {"jobs": {"j": {"steps": [{"run": "image_digest=abc"}]}}}}),
    ("image_digest:      lowercase YAML output key",
     {"b.yml": {"jobs": {"j": {"outputs": {"image_digest": "x"},
                               "steps": [{"run": "echo hi"}]}}}}),
):
    n = len(C.check_digest_is_consumed(doc))
    seen = "export DETECTED" if n else "export NOT detected -> unanalysed"
    print("    %-62s %s" % (label, seen))
DEFECTS.append(("INV-5", "exporter detector is case-sensitive; IMAGE_DIGEST= unseen",
                "FALSE NEGATIVE"))

# --------------------------------------------------------------- INV-4 granularity
banner("B. INV-4 gate-detection granularity")
print("  The gate test is `re.search('workflow_run|require_security_scan|needs:', raw)`")
print("  over the WHOLE dumped workflow, so any `needs:` anywhere marks it gated.")
print("")

show("INV-4", "pushes :latest in a job with no needs; needs exists elsewhere",
     C.check_no_ungated_mutable_tag_push("x.yml", Y(
         "on: {push: {}}\n"
         "jobs:\n"
         "  lint: {steps: [{run: flake8}]}\n"
         "  report: {needs: [lint], steps: [{run: 'echo done'}]}\n"
         "  push-image: {steps: [{run: 'docker push registry/app:latest'}]}\n"
     )), True)

show("INV-4", "pushes :latest, needs nowhere (control, must fire)",
     C.check_no_ungated_mutable_tag_push("y.yml", Y(
         "on: {push: {}}\n"
         "jobs: {push-image: {steps: [{run: 'docker push registry/app:latest'}]}}\n"
     )), True)

show("INV-4", "gated by a genuine security dependency (must NOT fire)",
     C.check_no_ungated_mutable_tag_push("g.yml", Y(
         "on: {push: {}}\n"
         "jobs:\n"
         "  gate: {steps: [{run: 'python scripts/require_security_scan.py'}]}\n"
         "  push-image: {needs: [gate], steps: [{run: 'docker push registry/app:latest'}]}\n"
     )), False)

# --------------------------------------------------------------- INV-2 coverage
banner("C. INV-2 exit-status-swallowing coverage")
INV2_CASES = [
    ("bandit ... || echo       (the live shape)", "bandit -r app || echo logged", True),
    ("bandit ... || true", "bandit -r app || true", True),
    ("bandit ... || exit 0", "bandit -r app || exit 0", True),
    ("bandit ... ; true", "bandit -r app ; true", True),
    ("set +e, then bandit", "set +e\nbandit -r app", True),
    ("if ! bandit; then echo soft; fi", "if ! bandit -r app; then echo soft; fi", True),
    ("bandit ... || /bin/true", "bandit -r app || /bin/true", True),
    ("line continuation before ||", "bandit -r app \\\n  || true", True),
    ("bandit -r app            (clean, must NOT fire)", "bandit -r app", False),
]
for label, cmd, expect in INV2_CASES:
    doc = {"jobs": {"j": {"steps": [{"name": "scan", "run": cmd}]}}}
    show("INV-2", label, C.check_mandatory_scan_not_swallowed("z.yml", doc), expect)

# --------------------------------------------------------------- INV-3 coverage
banner("D. INV-3 tag-as-digest coverage")
INV3_CASES = [
    ('DIGEST=$(... || echo "$IMAGE_TAG")   the live shape',
     'DIGEST=$(aws ecr describe-images --output text || echo "$IMAGE_TAG")', True),
    ('DIGEST=$(... || echo "latest")       literal fallback',
     'DIGEST=$(aws ecr describe-images --output text || echo "latest")', True),
    ('DIGEST=${DIGEST:-$IMAGE_TAG}         parameter expansion',
     'DIGEST=$(aws ecr describe-images)\nDIGEST=${DIGEST:-$IMAGE_TAG}', True),
    ('IMAGE_DIGEST=$IMAGE_TAG              direct assignment',
     'IMAGE_DIGEST=$IMAGE_TAG', True),
    ('DIGEST=$(aws ecr describe-images)    clean, must NOT fire',
     'DIGEST=$(aws ecr describe-images --output text)', False),
]
for label, cmd, expect in INV3_CASES:
    doc = {"jobs": {"j": {"steps": [{"name": "build", "run": cmd}]}}}
    show("INV-3", label, C.check_no_tag_as_digest("w.yml", doc), expect)

# --------------------------------------------------------------- INV-1 coverage
banner("E. INV-1 evidence-retention edge cases")
show("INV-1", "job-level continue-on-error: upload cannot be skipped",
     C.check_evidence_retention("v.yml", Y(
         "jobs:\n"
         "  sast:\n"
         "    continue-on-error: true\n"
         "    steps:\n"
         "      - {name: scan, run: 'bandit -r app'}\n"
         "      - {name: up, uses: 'actions/upload-artifact@v4'}\n"
     )), False)

show("INV-1", "upload guarded by if: always() (control, must NOT fire)",
     C.check_evidence_retention("u.yml", Y(
         "jobs:\n"
         "  sast:\n"
         "    steps:\n"
         "      - {name: scan, run: 'bandit -r app'}\n"
         "      - {name: up, uses: 'actions/upload-artifact@v4', if: 'always()'}\n"
     )), False)

show("INV-1", "unguarded upload after a blocking scan (control, must fire)",
     C.check_evidence_retention("t.yml", Y(
         "jobs:\n"
         "  sast:\n"
         "    steps:\n"
         "      - {name: scan, run: 'bandit -r app'}\n"
         "      - {name: up, uses: 'actions/upload-artifact@v4'}\n"
     )), True)

# --------------------------------------------------------------- summary
banner("SUMMARY")
if not DEFECTS:
    print("  No divergence between behaviour and specification in these cases.")
else:
    print("  %d case(s) diverge from the invariant's own specification:" % len(DEFECTS))
    print("")
    print("    %-7s %-9s %s" % ("inv", "kind", "case"))
    print("    %-7s %-9s %s" % ("-" * 7, "-" * 9, "-" * 60))
    for group, label, kind in DEFECTS:
        short = "FALSE-NEG" if kind.startswith("FALSE N") else "FALSE-POS"
        print("    %-7s %-9s %s" % (group, short, label[:60]))
print("")
print("  A false negative means a real violation would ship unnoticed.")
print("  A false positive means a correct design would be reported as a violation.")
print("")
print("  NOTE: this audits the PROTOTYPE checker. It changes no repository file and")
print("        does not alter any invariant. No invariant was weakened here.")
