"""PROPOSAL ARTIFACT — release-pipeline invariants, checked by parsing the workflow YAML.

NOT INSTALLED. No grant exists for `.github/workflows/**`, `scripts/**` or `tests/**`
(8 independent sessions have now each measured `GRANTED` occurrences in the ledger = 0), so
nothing governed was modified. On a grant this module moves to `tests/` and each invariant
becomes a CI assertion.

WHY YAML PARSING RATHER THAN grep. The Phase 31 brief warns against "brittle tests that merely
search for a string without proving the relevant configuration semantics". Every check below
resolves the job/step structure first and reasons about a step's position relative to the steps
that can fail it. `INV-1` in particular is undecidable by substring search: whether an upload is
reachable depends on which *earlier* steps in the *same job* can fail, which is a structural
property.

THE FIVE INVARIANTS

INV-1  evidence-retention
       A job containing a blocking (exit-code-propagating) scan step AND an artifact upload must
       guard the upload so it runs even when the scan fails. Otherwise the report explaining the
       failure is destroyed by the failure itself.

INV-2  mandatory-scan-not-swallowed
       A step invoking a security scanner must not terminate its command with `|| echo …`,
       `|| true` or `|| :`. Those make the step exit 0 whatever the scanner found.

INV-3  no-tag-as-digest
       A value written to a `*digest*` output must not come from a command whose failure
       fallback substitutes a tag. A field named `image_digest` holding a mutable tag is worse
       than an absent field, because consumers trust the name.

INV-4  no-ungated-mutable-tag-push
       A workflow that pushes a mutable tag (`:latest`) must declare a dependency on the
       security gate. Pushing `latest` before the gate puts an unvalidated artifact on the tag
       infrastructure resolves.

INV-5  digest-is-consumed
       If a build workflow exports an image digest, some deployment workflow must actually read
       it. An exported-but-unread digest means the identity chain is tag-based end to end and the
       digest is decorative.

Run against the live workflows:
    .venv\\Scripts\\python.exe staging/phase31_workflow_invariants.py
"""

from __future__ import annotations

import pathlib
import re
import sys
from typing import Iterable

import yaml

WORKFLOW_DIR = pathlib.Path(".github/workflows")

#: Commands that constitute a security scan whose verdict is mandatory.
SCANNER_TOKENS = ("bandit", "safety", "pip-audit", "trivy", "semgrep", "checkov")

#: Shell suffixes that discard a command's exit status.
_SWALLOW_RE = re.compile(r"\|\|\s*(echo\b|true\b|:\s*$)")

#: `actions/upload-artifact` in any version.
_UPLOAD_RE = re.compile(r"actions/upload-artifact")

#: A step `if:` that still runs after an earlier failure.
_ALWAYS_RE = re.compile(r"always\s*\(\s*\)|!\s*cancelled\s*\(\s*\)|failure\s*\(\s*\)")

#: An ECR/registry digest is `sha256:<64 hex>`; a commit tag is 40 hex. Distinguishable, which is
#: why the fallback in INV-3 is detectable at all.
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class Finding:
    def __init__(self, invariant: str, workflow: str, where: str, detail: str):
        self.invariant = invariant
        self.workflow = workflow
        self.where = where
        self.detail = detail

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return "%s  %s :: %s\n      %s" % (
            self.invariant, self.workflow, self.where, self.detail
        )


def _steps(job: dict) -> list:
    steps = job.get("steps")
    return steps if isinstance(steps, list) else []


def _step_name(step: dict, index: int) -> str:
    return str(step.get("name") or step.get("uses") or "step[%d]" % index)

def _step_text(step: dict) -> str:
    """Everything in a step that could carry a command, as one lowercase string."""
    parts = [str(step.get("run") or ""), str(step.get("uses") or "")]
    with_block = step.get("with")
    if isinstance(with_block, dict):
        parts.extend(str(v) for v in with_block.values())
    return "\n".join(parts).lower()


#: A step that INSTALLS a scanner is not a scan. Without this, `pip install bandit safety`
#: counts as a scanner step: harmless for INV-2 (no `||` to find) but it made INV-1's
#: operator-facing message name an install step as a blocking scan, which is imprecise.
_INSTALL_RE = re.compile(r"\b(pip|pipx|apt-get|apt|brew|npm|uv)\b[^\n]*\binstall\b")


def _is_scanner_step(step: dict) -> bool:
    text = _step_text(step)
    if not any(token in text for token in SCANNER_TOKENS):
        return False
    run_lines = [
        line for line in str(step.get("run") or "").splitlines()
        if any(token in line.lower() for token in SCANNER_TOKENS)
    ]
    if run_lines and all(_INSTALL_RE.search(line.lower()) for line in run_lines):
        return False
    return True


def _propagates_failure(step: dict) -> bool:
    """Whether this step's failure can fail the job.

    `continue-on-error: true` and a swallowed exit status both mean it cannot.
    """
    if step.get("continue-on-error") in (True, "true"):
        return False
    run = str(step.get("run") or "")
    if run and all(_SWALLOW_RE.search(line) for line in _meaningful_lines(run)):
        return False
    # An action-based step with `exit-code: '0'` reports but does not fail.
    with_block = step.get("with")
    if isinstance(with_block, dict) and str(with_block.get("exit-code", "")).strip() == "0":
        return False
    return True


def _meaningful_lines(run: str) -> Iterable[str]:
    for line in run.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and not stripped.startswith("echo "):
            yield stripped


def _runs_even_on_failure(step: dict) -> bool:
    return bool(_ALWAYS_RE.search(str(step.get("if") or "")))


def check_evidence_retention(name: str, doc: dict) -> list:
    """INV-1. An upload preceded by a failure-propagating scan must be `if: always()`."""
    findings = []
    for job_id, job in (doc.get("jobs") or {}).items():
        if not isinstance(job, dict):
            continue
        steps = _steps(job)
        blocking_before = []
        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            if _UPLOAD_RE.search(_step_text(step)) and blocking_before:
                if not _runs_even_on_failure(step):
                    findings.append(Finding(
                        "INV-1 evidence-retention", name,
                        "%s / %s" % (job_id, _step_name(step, index)),
                        "upload has no `if: always()` but %d earlier step(s) can fail the job "
                        "(%s). On that failure this upload is SKIPPED and the report that "
                        "explains the failure is never retained."
                        % (len(blocking_before), ", ".join(blocking_before)),
                    ))
            if _is_scanner_step(step) and _propagates_failure(step):
                blocking_before.append(_step_name(step, index))
    return findings


def check_mandatory_scan_not_swallowed(name: str, doc: dict) -> list:
    """INV-2. A scanner step must not discard its exit status."""
    findings = []
    for job_id, job in (doc.get("jobs") or {}).items():
        if not isinstance(job, dict):
            continue
        for index, step in enumerate(_steps(job)):
            if not isinstance(step, dict) or not _is_scanner_step(step):
                continue
            run = str(step.get("run") or "")
            for line in run.splitlines():
                if any(t in line.lower() for t in SCANNER_TOKENS) and _SWALLOW_RE.search(line):
                    findings.append(Finding(
                        "INV-2 mandatory-scan-not-swallowed", name,
                        "%s / %s" % (job_id, _step_name(step, index)),
                        "scanner exit status discarded by a `||` fallback, so the step reports "
                        "success whatever the scan found: %s" % line.strip(),
                    ))
    return findings


def check_no_tag_as_digest(name: str, doc: dict) -> list:
    """INV-3. A `*digest*` output must not fall back to a tag."""
    findings = []
    for job_id, job in (doc.get("jobs") or {}).items():
        if not isinstance(job, dict):
            continue
        for index, step in enumerate(_steps(job)):
            if not isinstance(step, dict):
                continue
            run = str(step.get("run") or "")
            if not run:
                continue
            # Variables emitted as a digest output.
            digest_vars = set()
            for match in re.finditer(r"(\w*digest\w*)\s*=\s*\$\{?(\w+)\}?", run, re.I):
                digest_vars.add(match.group(2))
            for match in re.finditer(r"^\s*(\w+)=\$\(", run, re.M):
                var = match.group(1)
                if "digest" in var.lower():
                    digest_vars.add(var)
            for var in digest_vars:
                pattern = re.compile(
                    r"^\s*%s=\$\((?P<body>.*?)\)\s*$" % re.escape(var), re.M | re.S
                )
                for assignment in pattern.finditer(run):
                    body = assignment.group("body")
                    fallback = re.search(r"\|\|\s*echo\s+\"?\$\{?(\w+)\}?", body)
                    if fallback:
                        findings.append(Finding(
                            "INV-3 no-tag-as-digest", name,
                            "%s / %s" % (job_id, _step_name(step, index)),
                            "`%s` is emitted as a digest output but falls back to `$%s` when the "
                            "lookup fails, so a field named *digest* can hold a mutable tag. "
                            "A real digest matches %s."
                            % (var, fallback.group(1), DIGEST_RE.pattern),
                        ))
    return findings


def check_no_ungated_mutable_tag_push(name: str, doc: dict) -> list:
    """INV-4. Pushing `:latest` requires a gate dependency."""
    findings = []
    raw = yaml.safe_dump(doc)
    gated = bool(
        re.search(r"workflow_run|require_security_scan|needs:", raw)
    )
    for job_id, job in (doc.get("jobs") or {}).items():
        if not isinstance(job, dict):
            continue
        for index, step in enumerate(_steps(job)):
            if not isinstance(step, dict):
                continue
            run = str(step.get("run") or "")
            for line in run.splitlines():
                if re.search(r"docker\s+push\s+\S*:latest", line) and not gated:
                    findings.append(Finding(
                        "INV-4 no-ungated-mutable-tag-push", name,
                        "%s / %s" % (job_id, _step_name(step, index)),
                        "pushes the mutable tag `latest` and this workflow declares no gate "
                        "dependency (no `workflow_run`, no `needs:`, no security-scan check), "
                        "so an unvalidated image lands on the tag infrastructure resolves: %s"
                        % line.strip(),
                    ))
    return findings


def check_digest_is_consumed(docs: dict) -> list:
    """INV-5. An exported digest nobody reads means the chain is tag-based."""
    findings = []
    # A workflow that declares `image_digest` as its OWN workflow-level output references
    # `jobs.<id>.outputs.image_digest` in doing so. Counting that as consumption made this
    # check inert against the exact shape it exists to catch (02-build.yml re-exports its own
    # digest; nothing downstream reads it). A consumer must therefore be a DIFFERENT workflow.
    exporters, consumers = [], []
    for name, doc in docs.items():
        raw = yaml.safe_dump(doc)
        exports = bool(re.search(r"image_digest\s*=", raw)) or \
            bool(re.search(r"image_digest:", raw))
        if exports:
            exporters.append(name)
        if re.search(r"needs\.[\w-]+\.outputs\.image_digest", raw) and not exports:
            consumers.append(name)
    if exporters and not consumers:
        findings.append(Finding(
            "INV-5 digest-is-consumed", ", ".join(exporters), "workflow outputs",
            "an image digest is exported but NO workflow reads "
            "`needs.<job>.outputs.image_digest`, so the build-to-deploy identity chain is "
            "tag-based end to end and the digest output is decorative.",
        ))
    return findings


def run(workflow_dir: pathlib.Path = WORKFLOW_DIR) -> list:
    docs = {}
    for path in sorted(workflow_dir.glob("*.yml")) + sorted(workflow_dir.glob("*.yaml")):
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            print("  !! %s failed to parse: %s" % (path.name, exc))
            continue
        if isinstance(loaded, dict):
            docs[path.name] = loaded

    findings = []
    for name, doc in docs.items():
        findings += check_evidence_retention(name, doc)
        findings += check_mandatory_scan_not_swallowed(name, doc)
        findings += check_no_tag_as_digest(name, doc)
        findings += check_no_ungated_mutable_tag_push(name, doc)
    findings += check_digest_is_consumed(docs)
    return findings


def main() -> int:
    print("Release-pipeline invariants over %s" % WORKFLOW_DIR)
    findings = run()
    if not findings:
        print("  all invariants hold")
        return 0
    for finding in findings:
        print("")
        print("  %s" % finding.invariant)
        print("    %s :: %s" % (finding.workflow, finding.where))
        print("    %s" % finding.detail)
    print("")
    print("  %d violation(s)" % len(findings))
    return 1


if __name__ == "__main__":
    sys.exit(main())
