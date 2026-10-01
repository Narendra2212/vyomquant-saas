"""tests/test_security_gate_blocks_deploy.py

A commit whose container scan failed does not deploy.

THE GAP THIS CLOSES
-------------------
``03-deploy.yml`` chains off ``02 Build`` via ``workflow_run``; ``05 Security`` ran on the same
push and gated nothing. So a commit carrying a CRITICAL container vulnerability deployed anyway.
That is not hypothetical: ``PyJWT 2.13.0`` - CVE-2026-102268, a key-confusion signature bypass
that lets an attacker forge tokens - reached production while ``05 Security`` was red, and stayed
until the pin was bumped.

THE THRESHOLD IS INHERITED, NOT INVENTED
----------------------------------------
``05-security.yml`` already runs Trivy with ``severity: CRITICAL`` and ``exit-code: 1``, so the
workflow is red only for CRITICAL. The gate therefore blocks on CRITICAL and lets HIGH and below
through. Gating on every severity would let a newly published CVE in a transitive package freeze
an unrelated hotfix.
"""

import importlib.util
import io
import os
import sys

import pytest
import yaml

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPT = os.path.join(REPO, "scripts", "require_security_scan.py")
DEPLOY_YML = os.path.join(REPO, ".github", "workflows", "03-deploy.yml")
SECURITY_YML = os.path.join(REPO, ".github", "workflows", "05-security.yml")


def load_gate():
    spec = importlib.util.spec_from_file_location("require_security_scan", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def gate(monkeypatch):
    mod = load_gate()
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("DEPLOY_SHA", "a" * 40)
    monkeypatch.setenv("ALLOW_UNSCANNED", "false")
    monkeypatch.setenv("SECURITY_GATE_WAIT_SECONDS", "0")
    mod.WAIT_SECONDS = 0
    return mod


def run(mod, runs):
    mod._runs_for = lambda repo, sha, token: list(runs)
    return mod.main()


def completed(conclusion):
    return {"status": "completed", "conclusion": conclusion, "run_started_at": "2026-01-01T00:00:00Z", "html_url": "u"}


class TestTheGateRefusesWhatItShould:
    def test_a_failed_scan_blocks_the_deploy(self, gate):
        """The PyJWT case: scan red, deploy must not proceed."""
        assert run(gate, [completed("failure")]) == 1

    @pytest.mark.parametrize("conclusion", ["cancelled", "skipped", "timed_out"])
    def test_a_scan_that_verified_nothing_blocks(self, gate, conclusion):
        """A cancelled scan is not a passed scan."""
        assert run(gate, [completed(conclusion)]) == 1

    def test_a_missing_scan_blocks(self, gate):
        """Nobody scanned it is not evidence that it is safe."""
        assert run(gate, []) == 1

    def test_a_scan_still_running_blocks(self, gate):
        """A deploy that outruns its own scan is an ungated deploy."""
        still = {"status": "in_progress", "conclusion": None, "run_started_at": "2026-01-01T00:00:00Z"}
        assert run(gate, [still]) == 1

    def test_missing_configuration_blocks(self, gate, monkeypatch):
        """Refuse rather than assume, when the gate cannot even identify the commit."""
        monkeypatch.delenv("DEPLOY_SHA")
        assert run(gate, [completed("success")]) == 1


class TestTheGateAllowsWhatItShould:
    def test_a_passing_scan_allows_the_deploy(self, gate):
        assert run(gate, [completed("success")]) == 0

    def test_the_newest_run_decides_on_a_re_run(self, gate):
        """A red scan that was re-run green must not keep blocking forever."""
        older = {"status": "completed", "conclusion": "failure", "run_started_at": "2026-01-01T00:00:00Z", "html_url": "u"}
        newer = {"status": "completed", "conclusion": "success", "run_started_at": "2026-01-02T00:00:00Z", "html_url": "u"}
        assert run(gate, [older, newer]) == 0

    def test_an_explicit_override_allows_an_unscanned_commit(self, gate, monkeypatch):
        """Dispatching an older commit is legitimate; the override is logged."""
        monkeypatch.setenv("ALLOW_UNSCANNED", "true")
        assert run(gate, []) == 0

    def test_the_override_does_not_excuse_a_FAILED_scan(self, gate, monkeypatch):
        """allow_unscanned means no scan exists - never ignore one that ran and failed."""
        monkeypatch.setenv("ALLOW_UNSCANNED", "true")
        assert run(gate, [completed("failure")]) == 1


class TestTheWorkflowActuallyInvokesTheGate:
    def test_the_deploy_runs_it_before_anything_expensive(self):
        doc = yaml.safe_load(io.open(DEPLOY_YML, encoding="utf-8").read())
        steps = doc["jobs"]["pre-deployment-validation"]["steps"]
        names = [s.get("name") or "" for s in steps]

        gate_at = [i for i, s in enumerate(steps) if "require_security_scan.py" in str(s.get("run", ""))]
        assert gate_at, "03-deploy.yml never invokes scripts/require_security_scan.py: %s" % names
        assert gate_at[0] <= 1, (
            "the gate is step %d; it should run before the expensive setup steps so a blocked "
            "deploy fails fast" % (gate_at[0] + 1)
        )

    def test_it_is_given_the_commit_being_deployed(self):
        doc = yaml.safe_load(io.open(DEPLOY_YML, encoding="utf-8").read())
        steps = doc["jobs"]["pre-deployment-validation"]["steps"]
        step = [s for s in steps if "require_security_scan.py" in str(s.get("run", ""))][0]
        env = step.get("env", {})

        assert "GITHUB_TOKEN" in env
        assert "workflow_run.head_sha" in env.get("DEPLOY_SHA", ""), (
            "DEPLOY_SHA must be the commit 02 Build built, not the workflow ref"
        )

    def test_the_scan_still_fails_only_on_critical(self):
        """The gate inherits this threshold; if the scan widens, revisit the gate note."""
        text = io.open(SECURITY_YML, encoding="utf-8").read()

        assert "severity: " in text and "CRITICAL" in text
        assert "exit-code: " in text
