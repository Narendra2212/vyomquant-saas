"""tests/test_deploy04_paired_artifact_guard.py — DEPLOY-04

Six times a change to `backend_app/routers/**` or a migration merged without re-recording
the artifact describing it, and `main` went red AFTER the merge — so the PR that caused it
was already green and gone. Once (#309) that silently stopped every production deploy for
a day, because `03 Deploy` refuses any commit whose `01 PR Check` failed; the backend sat
13 commits stale and nothing reported it.

`.github/workflows/10-paired-artifacts.yml` is the guard. This file tests the guard,
because a CI job that never runs is indistinguishable from one that always passes, and the
failure mode here is silence.

The shell logic itself is exercised by running the same `grep -E` patterns against
synthetic file lists, so a pattern that stops matching — the one way this guard could go
quietly inert — fails here rather than six merges later.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
WF = REPO / ".github" / "workflows" / "10-paired-artifacts.yml"

#: The label that downgrades a failure to a warning. Named here so a rename has to be
#: deliberate in two places rather than silently disabling every override.
OVERRIDE_LABEL = "no-baseline-change-needed"


@pytest.fixture(scope="module")
def doc():
    return yaml.safe_load(WF.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def body():
    return WF.read_text(encoding="utf-8")


# ───────────────────────── the trigger ─────────────────────────


def test_the_workflow_exists_at_all() -> None:
    """Recommended six times before it was built; its absence is the default state."""
    assert WF.exists(), f"{WF} is missing — the paired-artifact guard is unbuilt again"


def test_it_runs_on_pull_request_not_push(doc) -> None:
    """Catching the drift on `main` is what already happens and is too late: the PR that
    caused it is merged and green by then."""
    on = doc.get(True, doc.get("on"))
    assert "pull_request" in on, "the guard must run on the PR, before the merge"


def test_it_reacts_to_labels_being_added(doc) -> None:
    """The override is a label, so the job has to re-run when one is applied — otherwise
    adding the label leaves the old failure standing and the author cannot proceed."""
    on = doc.get(True, doc.get("on"))
    types = on["pull_request"].get("types", [])
    for needed in ("labeled", "unlabeled"):
        assert needed in types, (
            f"'{needed}' is not a trigger type, so applying the override label would not "
            "re-run this job and the PR would stay blocked"
        )


@pytest.mark.parametrize(
    "path",
    [
        "backend_app/routers/**",
        "backend_app/migrations/**",
        "migrations/**",
        "scripts/provision_database.py",
    ],
)
def test_every_watched_path_is_in_the_trigger(doc, path: str) -> None:
    """Each of these has caused a real incident. `scripts/provision_database.py` is
    included because it GENERATES the snapshot — changing it changes the expected output
    without any migration changing at all."""
    on = doc.get(True, doc.get("on"))
    assert path in on["pull_request"]["paths"], f"{path} is no longer watched"


def test_it_needs_full_history(doc) -> None:
    """A shallow fetch cannot find the merge base, and a long-lived branch would then
    diff as 'every file changed' — which reads as a pass, not a failure."""
    steps = doc["jobs"]["paired"]["steps"]
    checkout = next(s for s in steps if "checkout" in str(s.get("uses", "")))
    assert checkout["with"]["fetch-depth"] == 0


def test_it_diffs_against_the_merge_base(body) -> None:
    """A two-dot diff against the base BRANCH attributes other people's merges to this PR,
    which produces false passes — the worst outcome for a guard."""
    assert "merge-base" in body, "the guard does not compute a merge base"


def test_it_installs_nothing(doc) -> None:
    """The argument for making this required is that it is nearly free. A pip install or a
    database service would make it slow enough to be worth skipping."""
    steps = doc["jobs"]["paired"]["steps"]
    joined = " ".join(str(s.get("run", "")) + str(s.get("uses", "")) for s in steps)
    for forbidden in ("pip install", "npm ci", "services:", "setup-node"):
        assert forbidden not in joined, (
            f"the guard now does '{forbidden}', which removes the reason it can be required"
        )


# ───────────────────── the matching logic ─────────────────────
#
# The patterns are extracted from the workflow and run against synthetic file lists, so a
# pattern that stops matching fails HERE rather than at the sixth merge.


def _patterns(body: str) -> dict:
    """The `has '<regex>'` calls, in order, as the shell sees them."""
    found = re.findall(r"has '([^']+)'", body)
    assert found, "no `has '<regex>'` calls found; the guard's logic has been rewritten"
    return found


def _matches(pattern: str, paths: list) -> bool:
    rx = re.compile(pattern)
    return any(rx.search(p) for p in paths)


def test_the_router_pattern_matches_a_router_change(body) -> None:
    pats = _patterns(body)
    router = next(p for p in pats if "routers" in p)
    assert _matches(router, ["backend_app/routers/support.py"])
    assert not _matches(router, ["algo22-terminal/src/pages/Billing.jsx"])


def test_the_surface_pattern_matches_a_recorded_surface(body) -> None:
    pats = _patterns(body)
    surface = next(p for p in pats if "surface_" in p)
    assert _matches(surface, ["tests/regression/baseline/surface_support.json"])
    assert _matches(surface, ["tests/regression/baseline/surface_profile.json"])
    # A surface-looking file somewhere else must not satisfy it.
    assert not _matches(surface, ["docs/surface_support.json"])


def test_the_migration_pattern_matches_both_migration_trees(body) -> None:
    """Migrations live in TWO places — `backend_app/migrations/` and top-level
    `migrations/hardening/` — and the hardening file is one that actually caused this."""
    pats = _patterns(body)
    mig = next(p for p in pats if "migrations/" in p and "surface" not in p)
    assert _matches(mig, ["backend_app/migrations/074_mkt10_revoke_backtest_dml.sql"])
    assert _matches(mig, ["migrations/hardening/999_privilege_hardening.sql"])


def test_the_snapshot_pattern_matches_the_snapshot(body) -> None:
    pats = _patterns(body)
    snap = next(p for p in pats if "provisioning_snapshot" in p)
    assert _matches(snap, ["staging/provisioning_snapshot.json"])
    assert not _matches(snap, ["staging/provisioning_snapshot.json.bak"])


@pytest.mark.parametrize(
    "incident,changed,should_block",
    [
        # #309: the support surface changed and its baseline did not.
        ("#309 SUP-02", ["backend_app/routers/support.py"], True),
        # the same PR done correctly.
        ("#309 fixed", ["backend_app/routers/support.py",
                        "tests/regression/baseline/surface_support.json"], False),
        # #327: the hardening file changed and the snapshot did not (mine).
        ("#327 hardening", ["migrations/hardening/999_privilege_hardening.sql"], True),
        ("#327 fixed", ["migrations/hardening/999_privilege_hardening.sql",
                        "staging/provisioning_snapshot.json"], False),
        # #329: migration 075 changed and the snapshot did not.
        ("#329 risk06", ["backend_app/migrations/075_risk06.sql"], True),
        # a frontend-only PR must not be blocked by this at all.
        ("frontend only", ["algo22-terminal/src/pages/Strategies.jsx"], False),
    ],
)
def test_it_blocks_the_real_incidents_and_not_innocent_prs(
    body, incident: str, changed: list, should_block: bool
) -> None:
    """Replays each incident's actual file list through the guard's own patterns.

    This is the test that would have saved six red mains, so it is written as the
    incidents rather than as abstract cases.
    """
    pats = _patterns(body)
    router = next(p for p in pats if "routers" in p)
    surface = next(p for p in pats if "surface_" in p)
    mig = next(p for p in pats if "migrations/" in p and "surface" not in p)
    snap = next(p for p in pats if "provisioning_snapshot" in p)

    blocked = (
        (_matches(router, changed) and not _matches(surface, changed))
        or (_matches(mig, changed) and not _matches(snap, changed))
    )
    assert blocked is should_block, (
        f"{incident}: guard {'blocked' if blocked else 'allowed'} "
        f"{changed}, expected {'blocked' if should_block else 'allowed'}"
    )


# ───────────────────── the override ─────────────────────


def test_the_override_label_is_the_documented_one(body) -> None:
    assert OVERRIDE_LABEL in body, (
        f"the override label is no longer '{OVERRIDE_LABEL}'. If it was renamed, rename it "
        "here too — a stale name means every override silently fails to apply."
    )


def test_the_override_warns_rather_than_passing_silently(body) -> None:
    """An override that leaves no trace is indistinguishable from the guard not running."""
    override_region = body.split(OVERRIDE_LABEL, 1)[1][:400]
    assert "::warning" in override_region, (
        "the override exits 0 without a warning annotation, so a bypassed guard leaves no "
        "record on the run"
    )


def test_the_failure_explains_both_remedies(body) -> None:
    """A gate that says only 'failed' gets disabled. This one has to say what to do."""
    assert "re-record" in body
    assert OVERRIDE_LABEL in body
    assert "Do not disable this job" in body


def test_the_failure_message_records_the_checksum_trap(body) -> None:
    """`provision_database.py` digests `read_text`, so the checksum is over LF-normalised
    TEXT. A raw-bytes digest fails on any CRLF checkout, and that cost real time once."""
    assert "LF-NORMALISED" in body or "LF-normalised" in body, (
        "the guard no longer warns that the snapshot checksum is over normalised text"
    )
