"""Task 4.2 regression test: no publishable release artifact is a placeholder.

Three files under ``algo22-terminal/public/releases/`` were 64, 67 and 69 byte text
files named ``.AppImage``, ``.deb`` and ``.dmg``. Vite copies ``public/`` into ``dist/``
and CI syncs ``dist/`` to S3, so those bytes were publishable as desktop installers.

Scope. A ``releases/`` path is *publishable* when it reaches S3, which means under
``public/`` (Vite copies it) or under ``dist/`` (CI syncs it). ``algo22-terminal/releases/``
is a sibling of ``public/`` that Vite never copies; it holds the real 112 MB Windows
installer alongside three placeholders of its own, and it is out of scope here for the
same reason the real installer is. What matters is that nothing from it reaches ``dist/``,
which the ``dist/`` half of this scan already asserts.

Two assertions, matching the two halves of the fix:

1. No publishable release artifact is implausibly small for its extension. This catches a
   placeholder that has been copied back in.
2. ``06-frontend-deploy.yml`` carries the size gate, positioned after the build and before
   the S3 sync, so CI fails the deploy rather than publishing. The tree check alone cannot
   hold: ``releases/`` is gitignored, so nothing stops a placeholder reappearing in a
   working tree, and only the gate sees it at deploy time.

_Requirements: 1.30, 2.30, 3.6_
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TERMINAL = REPO_ROOT / "algo22-terminal"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "06-frontend-deploy.yml"

# Roots whose releases/ contents reach S3: public/ is copied into dist/ by Vite, and
# dist/ is what `aws s3 sync dist/` publishes.
PUBLISHABLE_RELEASE_ROOTS = (
    TERMINAL / "public" / "releases",
    TERMINAL / "dist" / "releases",
)

# Generous floors. The real Windows installer is 112,117,309 bytes, so a genuine build
# clears these by a wide margin; only a file that is not a build output trips them.
MIN_BYTES_BY_SUFFIX = {
    ".exe": 20 * 1024 * 1024,
    ".dmg": 20 * 1024 * 1024,
    ".AppImage": 10 * 1024 * 1024,
    ".deb": 10 * 1024 * 1024,
}


def _installers_under(root: Path):
    """Yield files below ``root`` whose extension carries a size floor."""
    if not root.exists():
        return
    for path in root.rglob("*"):
        if path.is_file() and path.suffix in MIN_BYTES_BY_SUFFIX:
            yield path


def test_no_publishable_release_artifact_is_implausibly_small():
    """Nothing S3-bound under a releases/ path is too small to be a real build."""
    undersized = []

    for root in PUBLISHABLE_RELEASE_ROOTS:
        for path in _installers_under(root):
            size = path.stat().st_size
            minimum = MIN_BYTES_BY_SUFFIX[path.suffix]
            if size < minimum:
                undersized.append(
                    f"{path.relative_to(REPO_ROOT).as_posix()}: {size} bytes "
                    f"(minimum {minimum} for {path.suffix})"
                )

    assert not undersized, (
        "Placeholder release artifact(s) on a publishable path; these would be synced to "
        "S3 and served as installers:\n  " + "\n  ".join(undersized)
    )


def test_real_windows_installer_is_untouched():
    """The genuine 112 MB installer lives outside public/ and is not part of this defect."""
    installer = TERMINAL / "releases" / "windows" / "VyomQuant-Setup-0.1.0.exe"
    if not installer.exists():
        pytest.skip("Windows installer absent from this checkout (releases/ is gitignored)")
    assert installer.stat().st_size >= MIN_BYTES_BY_SUFFIX[".exe"], (
        "The real Windows installer has shrunk below the plausibility floor"
    )


def _workflow_text() -> str:
    assert WORKFLOW.exists(), f"Missing deploy workflow at {WORKFLOW}"
    return WORKFLOW.read_text(encoding="utf-8")


def test_deploy_workflow_carries_the_size_gate():
    """The gate exists and states both size floors for all four installer types."""
    text = _workflow_text()
    assert "Reject implausible release artifacts" in text, (
        "06-frontend-deploy.yml has no release-artifact size gate; a placeholder in dist/ "
        "would be synced to S3"
    )
    assert "20971520" in text, "Gate is missing the 20 MiB floor for .exe/.dmg"
    assert "10485760" in text, "Gate is missing the 10 MiB floor for .AppImage/.deb"
    for suffix in MIN_BYTES_BY_SUFFIX:
        assert suffix in text, f"Gate does not cover {suffix}"


def test_size_gate_runs_after_build_and_before_s3_sync():
    """Ordering is the whole point: the gate must block the sync, not follow it."""
    text = _workflow_text()
    build = text.index("name: Build frontend")
    gate = text.index("name: Reject implausible release artifacts")
    sync = text.index("aws s3 sync dist/")

    assert build < gate, "Gate runs before the build, so dist/ does not exist yet"
    assert gate < sync, (
        "Gate runs after the S3 sync, so a placeholder is already published by the time it fires"
    )
