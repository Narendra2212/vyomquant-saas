"""Failure-mode regression suite for the D-P15-4 proposal gate. PROPOSAL ARTIFACT.

NOT INSTALLED under `tests/`. `pytest.ini` sets `testpaths = tests`, so this file is not
collected by the default lane and cannot disturb any concurrent workstream. Run explicitly:

    .venv\\Scripts\\python.exe -m pytest staging/phase17_test_frontend_release_gate.py

Every GitHub API interaction is a deterministic in-process fake — no network, no token, no
secret, no CI dispatch. The point of this suite is NOT that the happy path works; it is that
each historical and enumerated failure mode is REFUSED. The case that matters most is
`test_refuses_the_actual_ecd23a9e_incident`, which replays the real published-while-red
condition and asserts the gate would have stopped it.
"""

from __future__ import annotations

import pathlib
import sys
import urllib.error

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import phase17_frontend_release_gate as gate  # noqa: E402

REPO = "Narendra2212/vyomquant-saas"
TOKEN = "fake-token-not-a-credential"
SHA = "ecd23a9e934ec58a7b1f0e5db638da8e865e8187"
OTHER_SHA = "074b772ab16cece62cbfca0f2b78497be2ae56d5"


def _run(name, conclusion="success", sha=SHA, branch="main",
         status="completed", started="2026-10-11T09:48:44Z", run_id=1):
    return {
        "id": run_id, "name": name, "conclusion": conclusion, "status": status,
        "head_sha": sha, "head_branch": branch, "run_started_at": started,
    }


def _ok_runs():
    """One successful run for every required workflow, all on the candidate."""
    return [_run(n, run_id=i) for i, n in enumerate(gate.REQUIRED_WORKFLOWS, start=1)]


@pytest.fixture
def api(monkeypatch):
    """Install a fake runs-listing payload. Returns a setter."""
    def _install(runs, total_count=None):
        payload = {
            "total_count": len(runs) if total_count is None else total_count,
            "workflow_runs": runs,
        }
        monkeypatch.setattr(gate, "_api_get", lambda path, token: payload)
    return _install


def _refusal(*args, **kwargs):
    with pytest.raises(gate.GateRefusal) as caught:
        gate.verify(*args, **kwargs)
    return str(caught.value)


# ─── the one case that must pass ───────────────────────────────────────────────────────

def test_admits_a_candidate_with_all_required_evidence(api):
    api(_ok_runs())
    admitted = gate.verify(REPO, SHA, TOKEN)
    assert len(admitted) == len(gate.REQUIRED_WORKFLOWS)
    assert {r["name"] for r in admitted} == set(gate.REQUIRED_WORKFLOWS)


# ─── the historical incident ───────────────────────────────────────────────────────────

def test_refuses_the_actual_ecd23a9e_incident(api):
    """Replays the real event: `05 Security` FAILED and the frontend published anyway.

    Run 38129699454 concluded failure for this SHA while run 38129699497
    (`06 Frontend Deploy`) succeeded and pushed to S3 + CloudFront. The gate must refuse.
    """
    api([
        _run("02 Build", "success", run_id=38129699471),
        _run("05 Security", "failure", run_id=38129699454),
        _run("07 Desktop Build (native, unsigned)", "success", run_id=38129699516),
    ])
    message = _refusal(REPO, SHA, TOKEN)
    assert "05 Security" in message
    assert "failure" in message


def test_refuses_when_pr_check_never_ran_for_the_candidate(api):
    """`01 PR Check` is `pull_request`-only, so a direct push to main has no run at all.

    This is the second half of the historical incident and the reason D-P15-3 exists. The
    gate must treat "nobody ran it" as a refusal, not as an absence of bad news.
    """
    api([_run("05 Security", "success")])
    message = _refusal(REPO, SHA, TOKEN)
    assert "01 PR Check" in message
    assert "not evidence" in message


# ─── stale / mismatched evidence ───────────────────────────────────────────────────────

def test_refuses_evidence_belonging_to_another_sha(api):
    """An older green run for a DIFFERENT commit must not release this one."""
    api([_run(n, "success", sha=OTHER_SHA) for n in gate.REQUIRED_WORKFLOWS])
    # The API is filtered by head_sha, so this models filtering drift or a hand-rolled query.
    assert "another commit" in _refusal(REPO, SHA, TOKEN)


def test_a_newer_failure_overrides_an_older_success_for_the_same_sha(api):
    """THE SUBTLE ONE. `any(success)` would release here; newest-wins must refuse."""
    api(_ok_runs() + [
        _run("05 Security", "failure", started="2026-10-11T10:38:58Z", run_id=38132697285)
    ])
    message = _refusal(REPO, SHA, TOKEN)
    assert "05 Security" in message and "failure" in message


def test_a_newer_success_does_override_an_older_failure(api):
    """The converse must still pass, or a re-run could never unblock a release."""
    api(_ok_runs() + [
        _run("05 Security", "failure", started="2026-10-11T08:00:00Z", run_id=11),
        _run("05 Security", "success", started="2026-10-11T12:00:00Z", run_id=12),
    ])
    admitted = gate.verify(REPO, SHA, TOKEN)
    assert any(r["id"] == 12 for r in admitted)


# ─── non-success conclusions ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "skipped", "timed_out",
                                        "stale", "action_required", "neutral"])
def test_refuses_every_non_success_conclusion(api, conclusion):
    runs = [_run(n, "success") for n in gate.REQUIRED_WORKFLOWS[1:]]
    runs.append(_run(gate.REQUIRED_WORKFLOWS[0], conclusion))
    api(runs)
    assert conclusion in _refusal(REPO, SHA, TOKEN)


def test_refuses_a_null_conclusion(api):
    runs = [_run(n, "success") for n in gate.REQUIRED_WORKFLOWS[1:]]
    runs.append(_run(gate.REQUIRED_WORKFLOWS[0], None))
    api(runs)
    assert "no conclusion" in _refusal(REPO, SHA, TOKEN)


def test_refuses_a_run_still_in_progress(api):
    runs = [_run(n, "success") for n in gate.REQUIRED_WORKFLOWS[1:]]
    runs.append(_run(gate.REQUIRED_WORKFLOWS[0], None, status="in_progress"))
    api(runs)
    assert "no verdict exists yet" in _refusal(REPO, SHA, TOKEN)


# ─── branch scoping ────────────────────────────────────────────────────────────────────

def test_refuses_a_run_from_a_branch_outside_the_allow_list(api):
    api([_run(n, "success", branch="feature/whatever") for n in gate.REQUIRED_WORKFLOWS])
    assert "not in the allowed set" in _refusal(REPO, SHA, TOKEN)


def test_refuses_a_run_with_no_branch(api):
    api([_run(n, "success", branch=None) for n in gate.REQUIRED_WORKFLOWS])
    assert "not in the allowed set" in _refusal(REPO, SHA, TOKEN)


# ─── malformed input ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", ["", "   ", "ecd23a9e", "ECD23A9E934EC58A7B1F0E5DB638DA8E865E8187",
                                 "zzz23a9e934ec58a7b1f0e5db638da8e865e8187", "HEAD", "main"])
def test_refuses_a_missing_or_malformed_sha(api, bad):
    api(_ok_runs())
    with pytest.raises(gate.GateRefusal):
        gate.verify(REPO, bad.strip(), TOKEN)


def test_refuses_a_missing_token(api):
    api(_ok_runs())
    assert "unauthenticated" in _refusal(REPO, SHA, "")


def test_refuses_an_empty_repository(api):
    api(_ok_runs())
    assert "Repository is empty" in _refusal("", SHA, TOKEN)


def test_refuses_an_empty_required_workflow_policy(api, monkeypatch):
    """An empty policy must be a misconfiguration, never a pass."""
    api(_ok_runs())
    monkeypatch.setattr(gate, "REQUIRED_WORKFLOWS", ())
    assert "empty policy is a misconfiguration" in _refusal(REPO, SHA, TOKEN)


# ─── API failure modes ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("code", [401, 403, 404, 422, 429, 500, 502, 503])
def test_fails_closed_on_any_http_error(monkeypatch, code):
    """403 is the missing-`actions: read`-permission case specifically."""
    def _raise(path, token):
        raise urllib.error.HTTPError(gate._API + path, code, "err", {}, None)
    monkeypatch.setattr(gate, "_api_get", _raise)
    message = _refusal(REPO, SHA, TOKEN)
    assert str(code) in message and "Refusing" in message


def test_fails_closed_when_the_api_is_unreachable(monkeypatch):
    def _raise(path, token):
        raise urllib.error.URLError("dns failure")
    monkeypatch.setattr(gate, "_api_get", _raise)
    assert "unreachable" in _refusal(REPO, SHA, TOKEN)


def test_fails_closed_on_unparseable_json(monkeypatch):
    def _raise(path, token):
        raise ValueError("not json")
    monkeypatch.setattr(gate, "_api_get", _raise)
    assert "unparseable" in _refusal(REPO, SHA, TOKEN)


def test_fails_closed_on_a_non_object_payload(monkeypatch):
    monkeypatch.setattr(gate, "_api_get", lambda path, token: ["not", "a", "dict"])
    assert "non-object payload" in _refusal(REPO, SHA, TOKEN)


def test_fails_closed_when_workflow_runs_key_is_absent(monkeypatch):
    monkeypatch.setattr(gate, "_api_get", lambda path, token: {"total_count": 3})
    assert "no usable 'workflow_runs'" in _refusal(REPO, SHA, TOKEN)


# ─── pagination ────────────────────────────────────────────────────────────────────────

def test_fails_closed_when_total_count_exceeds_returned_runs(api):
    """The API says there are more runs than it handed back: the newest may be unseen."""
    api(_ok_runs(), total_count=250)
    assert "Pagination is ambiguous" in _refusal(REPO, SHA, TOKEN)


def test_fails_closed_when_the_page_is_full(api):
    """A full page means a newer run may sit on page 2."""
    runs = [_run("05 Security", "success", run_id=i) for i in range(gate._PER_PAGE)]
    api(runs, total_count=gate._PER_PAGE)
    assert "fills the page" in _refusal(REPO, SHA, TOKEN)


# ─── no override path ──────────────────────────────────────────────────────────────────

def test_there_is_no_environment_override(monkeypatch, api):
    """A manual dispatch must not be able to bypass the requirement.

    `require_security_scan.py` honours `ALLOW_UNSCANNED`; this gate must not grow an
    equivalent. Asserted structurally so adding one breaks this test.
    """
    source = pathlib.Path(gate.__file__).read_text(encoding="utf-8")
    for forbidden in ("ALLOW_UNSCANNED", "ALLOW_UNVERIFIED", "SKIP_GATE", "FORCE_DEPLOY"):
        assert forbidden not in source.replace(
            "`require_security_scan.py` offers `ALLOW_UNSCANNED`", ""
        ), "an override escape hatch appeared in the gate: %s" % forbidden

    api([_run("02 Build", "success"), _run("05 Security", "failure")])
    monkeypatch.setenv("ALLOW_UNSCANNED", "true")
    monkeypatch.setenv("FORCE_DEPLOY", "1")
    assert "Refusing" in _refusal(REPO, SHA, TOKEN)
