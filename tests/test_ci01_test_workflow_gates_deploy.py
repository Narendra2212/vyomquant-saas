"""CI-01: a commit whose test workflow did not pass does not deploy."""
import importlib.util
import os

import pytest
import yaml

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPT = os.path.join(REPO, "scripts", "require_tests_passed.py")
WF = os.path.join(REPO, ".github", "workflows")


@pytest.fixture
def gate(monkeypatch):
    spec = importlib.util.spec_from_file_location("require_tests_passed", SCRIPT)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    for k, v in {"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": "o/r", "DEPLOY_SHA": "a" * 40, "ALLOW_UNTESTED": "false"}.items():
        monkeypatch.setenv(k, v)
    mod.WAIT_SECONDS = 0
    return mod


def _run(mod, runs):
    mod._runs_for = lambda *_: list(runs)
    return mod.main()


def _r(conclusion, status="completed"):
    return {"status": status, "conclusion": conclusion, "run_started_at": "2026-01-01T00:00:00Z", "html_url": "u"}


def test_green_run_passes(gate):
    assert _run(gate, [_r("success")]) == 0


@pytest.mark.parametrize("c", ["failure", "cancelled", "skipped", "timed_out", None])
def test_anything_but_success_refuses(gate, c):
    assert _run(gate, [_r(c)]) == 1


def test_missing_run_refuses_and_override_is_explicit(gate, monkeypatch):
    assert _run(gate, []) == 1
    monkeypatch.setenv("ALLOW_UNTESTED", "true")
    assert _run(gate, []) == 0


def test_still_running_after_the_wait_refuses(gate):
    assert _run(gate, [_r(None, status="in_progress")]) == 1


def test_latest_run_decides(gate):
    older = dict(_r("failure"), run_started_at="2026-01-01T00:00:00Z")
    newer = dict(_r("success"), run_started_at="2026-01-02T00:00:00Z")
    assert _run(gate, [older, newer]) == 0
    assert _run(gate, [dict(newer, conclusion="failure"), dict(older, conclusion="success", run_started_at="2025-12-01T00:00:00Z")]) == 1


def test_missing_credentials_refuse(gate, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN")
    assert gate.main() == 1


def test_deploy_workflow_runs_the_gate_before_building_anything():
    wf = yaml.safe_load(open(os.path.join(WF, "03-deploy.yml")))
    steps = wf["jobs"]["pre-deployment-validation"]["steps"]
    cmds = [s.get("run", "") for s in steps]
    i = next(k for k, c in enumerate(cmds) if "require_tests_passed.py" in c)
    j = next(k for k, c in enumerate(cmds) if "require_security_scan.py" in c)
    install = next(k for k, c in enumerate(cmds) if "pip install" in c)
    assert i < install and j < install
    assert "allow_untested" in wf[True]["workflow_dispatch"]["inputs"]


def test_test_workflow_also_runs_on_push_to_main_and_gate_name_matches():
    wf = yaml.safe_load(open(os.path.join(WF, "01-pr-check.yml")))
    triggers = wf[True]
    assert "push" in triggers and "main" in triggers["push"]["branches"]
    assert wf["name"] == "01 PR Check"  # the name the gate looks up
