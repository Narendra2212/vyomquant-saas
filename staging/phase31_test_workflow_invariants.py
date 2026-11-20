"""Tests for the Phase 31 release-pipeline invariants. PROPOSAL ARTIFACT.

Not under `tests/` — `pytest.ini` sets `testpaths = tests`, so this is not collected by the
default lane and cannot disturb a concurrent workstream. Run explicitly:

    .venv\\Scripts\\python.exe -m pytest staging/phase31_test_workflow_invariants.py

EVERY invariant gets BOTH controls:
  * a POSITIVE control — a synthetic workflow carrying the defect, which must be reported;
  * a NEGATIVE control — the corrected form of the same workflow, which must be silent.

The negative controls are what make this suite worth anything. A checker that fires on
everything is as useless as one that fires on nothing, and the negative control is also the
specification of the fix: whatever makes it pass is what the owner needs to apply.

Two further tests assert the checker's verdict on the LIVE workflows, so this file fails the
moment a real defect is fixed — at which point the expectation is updated deliberately rather
than the finding quietly disappearing.
"""

from __future__ import annotations

import pathlib
import re
import sys

import pytest
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import phase31_workflow_invariants as inv  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _doc(text: str) -> dict:
    return yaml.safe_load(text)


def _ids(findings) -> set:
    return {f.invariant.split()[0] for f in findings}


# ── INV-1 evidence retention ───────────────────────────────────────────────────────────

_INV1_BAD = """
name: sec
on: [push]
jobs:
  sast:
    steps:
      - name: Run Bandit Scan
        run: bandit -r backend_app -ll -f json -o reports/b.json
      - name: Upload Security Reports
        uses: actions/upload-artifact@v4
"""

_INV1_GOOD = """
name: sec
on: [push]
jobs:
  sast:
    steps:
      - name: Run Bandit Scan
        run: bandit -r backend_app -ll -f json -o reports/b.json
      - name: Upload Security Reports
        if: always()
        uses: actions/upload-artifact@v4
"""


def test_inv1_fires_when_the_upload_is_unguarded():
    findings = inv.check_evidence_retention("x.yml", _doc(_INV1_BAD))
    assert len(findings) == 1
    assert "SKIPPED" in findings[0].detail
    assert "Run Bandit Scan" in findings[0].detail


def test_inv1_silent_when_the_upload_is_if_always():
    assert inv.check_evidence_retention("x.yml", _doc(_INV1_GOOD)) == []


def test_inv1_silent_when_no_blocking_step_precedes_the_upload():
    """An upload with nothing that can fail before it is fine unguarded."""
    doc = _doc("""
name: x
on: [push]
jobs:
  j:
    steps:
      - name: Upload
        uses: actions/upload-artifact@v4
""")
    assert inv.check_evidence_retention("x.yml", doc) == []


def test_inv1_silent_when_the_scan_cannot_fail_the_job():
    """`continue-on-error` means the scan cannot skip the upload, so there is nothing to fix."""
    doc = _doc("""
name: x
on: [push]
jobs:
  j:
    steps:
      - name: Run Bandit Scan
        continue-on-error: true
        run: bandit -r backend_app -ll
      - name: Upload
        uses: actions/upload-artifact@v4
""")
    assert inv.check_evidence_retention("x.yml", doc) == []


# ── INV-2 swallowed scanner ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("suffix", ["|| echo 'advisory logged'", "|| true", "|| :"])
def test_inv2_fires_on_every_swallow_form(suffix):
    # The run value is QUOTED: an unquoted `|| :` makes `: ` a YAML mapping indicator and the
    # document fails to parse. The shell form is still exactly what CI would execute.
    doc = _doc("""
name: x
on: [push]
jobs:
  j:
    steps:
      - name: Run Safety Check
        run: "safety check --json --output reports/s.json %s"
""" % suffix)
    findings = inv.check_mandatory_scan_not_swallowed("x.yml", doc)
    assert len(findings) == 1
    assert "exit status discarded" in findings[0].detail


def test_inv2_silent_when_the_scanner_exit_status_is_kept():
    doc = _doc("""
name: x
on: [push]
jobs:
  j:
    steps:
      - name: Run Safety Check
        run: safety check --json --output reports/s.json
""")
    assert inv.check_mandatory_scan_not_swallowed("x.yml", doc) == []


def test_inv2_silent_for_a_non_scanner_command_with_a_fallback():
    """`docker image prune … || true` is housekeeping, not a security verdict."""
    doc = _doc("""
name: x
on: [push]
jobs:
  j:
    steps:
      - name: Free disk
        run: sudo docker image prune --all --force || true
""")
    assert inv.check_mandatory_scan_not_swallowed("x.yml", doc) == []


# ── INV-3 tag masquerading as a digest ─────────────────────────────────────────────────

_INV3_BAD = """
name: build
on: [push]
jobs:
  b:
    steps:
      - name: Push
        run: |
          DIGEST=$(aws ecr describe-images --query 'imageDetails[0].imageDigest' --output text 2>/dev/null || echo "$IMAGE_TAG")
          echo "image_digest=$DIGEST" >> $GITHUB_OUTPUT
"""

_INV3_GOOD = """
name: build
on: [push]
jobs:
  b:
    steps:
      - name: Push
        run: |
          DIGEST=$(aws ecr describe-images --query 'imageDetails[0].imageDigest' --output text)
          echo "image_digest=$DIGEST" >> $GITHUB_OUTPUT
"""


def test_inv3_fires_on_the_tag_fallback():
    findings = inv.check_no_tag_as_digest("build.yml", _doc(_INV3_BAD))
    assert len(findings) == 1
    assert "IMAGE_TAG" in findings[0].detail


def test_inv3_silent_without_a_fallback():
    assert inv.check_no_tag_as_digest("build.yml", _doc(_INV3_GOOD)) == []


def test_a_commit_tag_is_not_a_valid_digest():
    """The two are distinguishable, which is why the fallback is detectable and fixable."""
    assert inv.DIGEST_RE.match("sha256:" + "a" * 64)
    assert not inv.DIGEST_RE.match("ecd23a9e934ec58a7b1f0e5db638da8e865e8187")
    assert not inv.DIGEST_RE.match("latest")


# ── INV-4 ungated mutable tag ──────────────────────────────────────────────────────────

def test_inv4_fires_on_an_ungated_latest_push():
    doc = _doc("""
name: build
on: [push]
jobs:
  b:
    steps:
      - name: Push
        run: docker push $ECR_REGISTRY/$ECR_REPOSITORY:latest
""")
    findings = inv.check_no_ungated_mutable_tag_push("build.yml", doc)
    assert len(findings) == 1
    assert "unvalidated image" in findings[0].detail


def test_inv4_silent_when_the_push_is_behind_a_gate():
    doc = _doc("""
name: build
on:
  workflow_run:
    workflows: ["05 Security"]
    types: [completed]
jobs:
  b:
    steps:
      - name: Push
        run: docker push $ECR_REGISTRY/$ECR_REPOSITORY:latest
""")
    assert inv.check_no_ungated_mutable_tag_push("build.yml", doc) == []


def test_inv4_silent_when_only_an_immutable_tag_is_pushed():
    doc = _doc("""
name: build
on: [push]
jobs:
  b:
    steps:
      - name: Push
        run: docker push $ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG
""")
    assert inv.check_no_ungated_mutable_tag_push("build.yml", doc) == []


# ── INV-5 exported-but-unread digest ───────────────────────────────────────────────────

def test_inv5_fires_when_nothing_downstream_reads_the_digest():
    build = _doc("""
name: build
on: [push]
jobs:
  b:
    outputs:
      image_digest: ${{ steps.s.outputs.image_digest }}
    steps:
      - id: s
        run: echo "image_digest=$DIGEST" >> $GITHUB_OUTPUT
""")
    deploy = _doc("""
name: deploy
on: [push]
jobs:
  d:
    steps:
      - run: aws ecr describe-images --image-ids imageTag=$IMAGE_TAG
""")
    findings = inv.check_digest_is_consumed({"build.yml": build, "deploy.yml": deploy})
    assert len(findings) == 1
    assert "decorative" in findings[0].detail


def test_inv5_silent_when_a_downstream_workflow_reads_the_digest():
    build = _doc("""
name: build
on: [push]
jobs:
  b:
    steps:
      - run: echo "image_digest=$DIGEST" >> $GITHUB_OUTPUT
""")
    deploy = _doc("""
name: deploy
on: [push]
jobs:
  d:
    steps:
      - run: echo ${{ needs.b.outputs.image_digest }}
""")
    assert inv.check_digest_is_consumed({"build.yml": build, "deploy.yml": deploy}) == []


def test_inv5_does_not_count_a_workflow_as_its_own_consumer():
    """REGRESSION. This is the bug that made INV-5 inert on the live workflows.

    A workflow re-exporting its own job output references
    `jobs.<id>.outputs.image_digest`, which the first implementation counted as consumption.
    """
    build = _doc("""
name: build
on: [push]
jobs:
  b:
    outputs:
      image_digest: ${{ jobs.b.outputs.image_digest }}
    steps:
      - run: echo "image_digest=$DIGEST" >> $GITHUB_OUTPUT
""")
    findings = inv.check_digest_is_consumed({"build.yml": build})
    assert len(findings) == 1, (
        "a workflow that only re-exports its own digest must still be reported as unconsumed"
    )


# ── the live workflows ─────────────────────────────────────────────────────────────────

def test_the_live_workflows_currently_violate_exactly_the_five_known_invariants():
    """Pins the measured state. If a fix lands, this fails and is updated deliberately."""
    findings = inv.run(REPO_ROOT / ".github" / "workflows")
    assert _ids(findings) == {"INV-1", "INV-2", "INV-3", "INV-4", "INV-5"}, _ids(findings)
    by_workflow = {}
    for finding in findings:
        by_workflow.setdefault(finding.workflow, set()).add(finding.invariant.split()[0])
    # INV-5 is attributed to the EXPORTER, which is `02-build.yml`, so it appears there too.
    assert by_workflow.get("02-build.yml") == {"INV-3", "INV-4", "INV-5"}
    assert by_workflow.get("05-security.yml") == {"INV-1", "INV-2"}


def test_the_bandit_scan_itself_is_still_blocking():
    """A correction to one historical worry: Bandit's exit code is NOT swallowed.

    Only the Safety step is. Bandit is what actually reds `05 Security`, which is why the
    deploy gate refuses — and why the missing upload (INV-1) destroys the evidence.
    """
    doc = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "05-security.yml").read_text(encoding="utf-8")
    )
    steps = doc["jobs"]["sast-scan"]["steps"]
    # Match the step that INVOKES bandit, not the one that installs it — `pip install bandit
    # safety` also contains the word.
    bandit = [
        s for s in steps
        if re.search(r"(^|\s|&&\s*)bandit\s+-", str(s.get("run", "")), re.M)
    ]
    assert len(bandit) == 1
    assert inv._propagates_failure(bandit[0]), "Bandit must remain able to fail the job"
    assert "||" not in str(bandit[0]["run"])


def test_both_terraform_roots_are_scanned_blocking():
    """Another historical worry corrected: the IaC scan is NOT one chained command.

    `05-security.yml` runs two separate blocking Trivy steps, so the second root is not
    skipped by the first step's exit. Asserted so a regression to a chained form is caught.
    """
    doc = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "05-security.yml").read_text(encoding="utf-8")
    )
    steps = doc["jobs"]["iac-scan"]["steps"]
    blocking = [
        s for s in steps
        if "trivy" in str(s.get("uses", "")).lower()
        and str((s.get("with") or {}).get("exit-code", "")).strip() == "1"
    ]
    refs = {str((s.get("with") or {}).get("scan-ref", "")).strip() for s in blocking}
    assert len(blocking) == 2, "expected two blocking IaC scan steps, found %d" % len(blocking)
    assert refs == {"terraform", "aerora_quant_platform/infrastructure"}, refs
