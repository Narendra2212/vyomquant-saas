"""tests/test_deploy02_freshness_is_measured_not_assumed.py — DEPLOY-02

On 2026-11-17 the backend went stale at `b44628cd` and stayed there for a day while
`main` moved 13 commits ahead. The deploy was not failing -- it was REFUSING, correctly,
because `01 PR Check` was red on `main` after #309 merged without re-recording a
baseline artifact. A refusal that works as designed emits no alert, so the fleet quietly
stopped shipping and the only reason anyone found out was that someone went looking.

`06 Frontend Deploy` has the same gate AND is `paths`-filtered, so its version of that
outage produces no run at all -- no red X to notice. `scripts/check_deploy_freshness.py`
asks the question from outside instead of waiting to be told.

This file protects the two things that would make that script lie:

1. **Its idea of what triggers a frontend build drifting from the workflow's.** The
   script computes the frontend's expected SHA as "the newest commit touching one of
   `06`'s trigger paths". If someone adds a trigger path to the workflow and not to the
   script, it would compute `expected` from a stale premise and report a stale frontend
   as current -- the exact failure it exists to catch, silently reintroduced.
2. **The verdict logic itself**, including the distinction between "stale" and "I could
   not tell", which must never collapse into a pass.
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "check_deploy_freshness.py"
WORKFLOW = REPO / ".github" / "workflows" / "06-frontend-deploy.yml"


def _load():
    spec = importlib.util.spec_from_file_location("check_deploy_freshness", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


freshness = _load()


def _workflow_push_paths() -> list:
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    # PyYAML parses a bare `on:` key as the boolean True.
    on = doc.get(True, doc.get("on"))
    return list(on["push"]["paths"])


def _normalise(path: str) -> str:
    """A workflow path glob as the git pathspec the script uses.

    `algo22-terminal/**` and `algo22-terminal/` select the same commits for
    `git log -- <pathspec>`; a literal file path is already a pathspec.
    """
    if path.endswith("/**"):
        return path[: -len("**")]
    if path.endswith("/*"):
        return path[: -len("*")]
    return path


# ───────────────── 1. the script's premise matches the workflow ─────────────────


def test_every_workflow_trigger_path_is_known_to_the_freshness_check() -> None:
    """The drift that would make the check report a stale frontend as current."""
    declared = {_normalise(p) for p in _workflow_push_paths()}
    known = set(freshness.FRONTEND_TRIGGER_PATHS)

    missing = declared - known
    assert not missing, (
        f"06-frontend-deploy.yml triggers on {sorted(missing)}, which "
        "check_deploy_freshness.py does not know about. It would compute the frontend's "
        "expected commit from a stale idea of what starts a build, and report a stale "
        "frontend as current. Add them to FRONTEND_TRIGGER_PATHS."
    )


def test_the_freshness_check_claims_no_trigger_the_workflow_lacks() -> None:
    """The mirror image: an extra path makes `expected` too new and cries wolf.

    A check that cries wolf is one nobody reads, which is how the original outage went
    unnoticed for a day.
    """
    declared = {_normalise(p) for p in _workflow_push_paths()}
    extra = set(freshness.FRONTEND_TRIGGER_PATHS) - declared
    assert not extra, (
        f"check_deploy_freshness.py treats {sorted(extra)} as a frontend build trigger "
        "but 06-frontend-deploy.yml does not. It would expect a build that never "
        "happens and report a healthy frontend as stale."
    )


def test_the_workflow_is_still_paths_filtered() -> None:
    """If `06` ever deploys on every push, the frontend's expectation becomes main head.

    Pinned because the script's whole frontend branch exists to accommodate the filter.
    Removing the filter without revisiting the script would make the check too lenient:
    it would keep accepting the last `algo22-terminal/**` commit when it should now be
    demanding `main` head.
    """
    paths = _workflow_push_paths()
    assert paths, (
        "06-frontend-deploy.yml no longer filters on paths, so the frontend should now "
        "be expected to equal main head. Update check_deploy_freshness.py's frontend "
        "expectation and this test together."
    )


def test_the_backend_release_url_is_the_api_scoped_one() -> None:
    """`/release.json` would compare the frontend against itself and pass vacuously.

    CloudFront proxies only `/api/*`, `/ws/*`, `/health*`, `/metrics*` and
    `/openapi.json` to the ALB; a bare `/release.json` falls through to S3 and returns
    the FRONTEND's file. The workflow's own FE/BE comparison step records the same trap.
    """
    assert freshness.BACKEND_RELEASE == "/api/release.json"
    assert freshness.FRONTEND_RELEASE == "/release.json"
    assert freshness.BACKEND_RELEASE != freshness.FRONTEND_RELEASE


# ───────────────── 2. the verdict logic ─────────────────

_SHA_A = "a" * 40
_SHA_B = "b" * 40
_GRACE = timedelta(minutes=30)


@pytest.fixture(autouse=True)
def _no_git_lookups(monkeypatch):
    """Keep the verdict tests independent of this checkout's history.

    `assess` calls `describe`, `commits_between` and `is_ancestor`, all of which shell
    out to git. Stubbing them is what makes these cases about the DECISION rather than
    about whichever commits happen to exist locally.
    """
    monkeypatch.setattr(freshness, "describe", lambda sha: sha[:8])
    monkeypatch.setattr(freshness, "commits_between", lambda old, new: 3)
    monkeypatch.setattr(freshness, "is_ancestor", lambda a, d: False)


def _assess(deployed, expected, built_at=None, grace=_GRACE):
    return freshness.assess(
        "tier", deployed, built_at, expected, "because", grace
    )


def test_the_same_commit_is_current() -> None:
    assert _assess(_SHA_A, _SHA_A).state == "current"


def test_an_older_commit_with_no_build_time_is_stale() -> None:
    """No `built_at` means the grace window cannot apply, so the gap must be reported."""
    assert _assess(_SHA_A, _SHA_B, built_at=None).state == "stale"


def test_a_gap_older_than_the_grace_window_is_stale() -> None:
    old = datetime.now(timezone.utc) - timedelta(hours=6)
    assert _assess(_SHA_A, _SHA_B, built_at=old).state == "stale"


def test_a_fresh_gap_is_reported_as_in_flight_not_stale() -> None:
    """A deploy takes minutes; a gap younger than the window is probably one in progress."""
    recent = datetime.now(timezone.utc) - timedelta(minutes=5)
    assert _assess(_SHA_A, _SHA_B, built_at=recent).state == "in-flight"


def test_the_grace_boundary_is_inclusive_and_does_not_flip_to_stale() -> None:
    at_edge = datetime.now(timezone.utc) - _GRACE + timedelta(seconds=5)
    assert _assess(_SHA_A, _SHA_B, built_at=at_edge).state == "in-flight"


def test_a_deployed_commit_newer_than_expected_is_not_stale(monkeypatch) -> None:
    """A local checkout that has not fetched must not manufacture a false alarm."""
    monkeypatch.setattr(freshness, "is_ancestor", lambda a, d: True)
    v = _assess(_SHA_A, _SHA_B, built_at=datetime.now(timezone.utc) - timedelta(days=1))
    assert v.state == "current"
    assert any("newer than expected" in line for line in v.lines)


def test_the_stale_verdict_names_both_commits() -> None:
    """An alert that does not say WHICH commits sends the reader back to the shell."""
    v = _assess(_SHA_A, _SHA_B, built_at=None)
    rendered = v.render()
    assert _SHA_A[:8] in rendered
    assert _SHA_B[:8] in rendered
    assert "STALE" in rendered


# ───────────────── 3. "could not tell" is not a pass ─────────────────


def test_an_unreachable_endpoint_exits_two_not_zero(monkeypatch) -> None:
    """The distinction the original outage turned on.

    "I could not determine the deployed version" must not read as "the deployed version
    is fine". Exit 2, never 0.
    """
    monkeypatch.setattr(freshness, "_git", lambda *a: _SHA_A)
    monkeypatch.setattr(freshness, "describe", lambda sha: sha[:8])

    def _boom(url, timeout):
        raise freshness.Undetermined(f"{url}: connection refused")

    monkeypatch.setattr(freshness, "fetch_release", _boom)

    rc = freshness.main(["--tier", "backend"])
    assert rc == 2, "an unreachable release endpoint must not exit 0"


def test_a_stale_tier_exits_one(monkeypatch) -> None:
    monkeypatch.setattr(freshness, "_git", lambda *a: _SHA_B)
    monkeypatch.setattr(freshness, "describe", lambda sha: sha[:8])
    monkeypatch.setattr(freshness, "commits_between", lambda old, new: 13)
    monkeypatch.setattr(freshness, "is_ancestor", lambda a, d: False)
    monkeypatch.setattr(
        freshness, "fetch_release", lambda url, timeout: (_SHA_A, None)
    )

    rc = freshness.main(["--tier", "backend"])
    assert rc == 1


def test_a_current_tier_exits_zero(monkeypatch) -> None:
    monkeypatch.setattr(freshness, "_git", lambda *a: _SHA_A)
    monkeypatch.setattr(freshness, "describe", lambda sha: sha[:8])
    monkeypatch.setattr(
        freshness, "fetch_release", lambda url, timeout: (_SHA_A, None)
    )

    rc = freshness.main(["--tier", "backend"])
    assert rc == 0


def test_a_release_payload_without_a_sha_is_undetermined() -> None:
    """A 200 that carries no usable sha is not evidence of freshness."""
    import json as _json
    import urllib.request

    class _Resp:
        def __init__(self, body):
            self._body = body

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    original = urllib.request.urlopen
    try:
        urllib.request.urlopen = lambda req, timeout=None: _Resp(
            _json.dumps({"built_at": "2026-01-01T00:00:00Z"}).encode()
        )
        with pytest.raises(freshness.Undetermined):
            freshness.fetch_release("https://example.invalid/x", 1.0)
    finally:
        urllib.request.urlopen = original
