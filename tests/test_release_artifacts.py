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

# ═══════════════════════════════════════════════════════════════════════════════
# Task 4.3: no download link is rendered for a platform with no artifact
# ═══════════════════════════════════════════════════════════════════════════════
#
# The other half of requirement 1.30. Task 4.2 above stops a placeholder BYTE SET from
# being published; this stops the SURFACE from advertising one. All four advertised URLs
# return 403 — CI's ``dist/`` carries no ``releases/`` directory, so ``aws s3 sync dist/
# --delete`` deletes that prefix from the bucket on every deploy — and the page rendered
# four hardcoded sizes (84.2 / 78.5 / 75.4 / 68.2 MB) and four SHA-256 strings beside them.
#
# WHY THIS HALF IS HERE AND NOT ONLY IN VITEST
# --------------------------------------------
# The claim "the card renders the not-available marker and no link" is a claim about a
# rendered DOM, and it is asserted where a DOM exists:
# ``algo22-terminal/tests/unit/pages/downloadSurface.test.jsx`` mounts both surfaces, reads
# ``data-panel-state``, and checks every anchor. Restating that in Python would mean
# regexing JSX to guess at what renders, which is a weaker assertion wearing the same words.
#
# What Python adds instead is the half that is genuinely a file-set property, and it is
# broader than the two files the vitest suite mounts: **no component anywhere holds an
# installer URL, a hardcoded artifact size, or a checksum.** A link cannot be rendered from
# a URL the tree does not contain. This file is also what wave 0's checkpoint runs, so the
# cheap scan lands in the suite that gates the release.
#
# _Requirements: 1.30, 2.30, 3.6, 3.7_

import re

COMPONENTS = TERMINAL / "src" / "components"
PAGE_FIELDS = TERMINAL / "src" / "design" / "pageFields.js"

# The four platform keys, which are both the declaration's `field` values and the download
# page's own tab ids.
WITHDRAWN_PLATFORMS = ("windows", "macos", "linuxAppImage", "linuxDeb")

# The four figures that used to render beside the four links.
WITHDRAWN_SIZES = ("84.2 MB", "78.5 MB", "75.4 MB", "68.2 MB")

# `releases/windows/...`, however it is quoted or spelled.
RELEASES_PATH = re.compile(r"releases/(?:windows|mac|linux)/")

# A checksum advertised for a file that is not served. Two of the four were the hashes of
# the empty string.
CHECKSUM_LITERAL = re.compile(r"sha-?256\s*:\s*[0-9a-f]{32,}", re.IGNORECASE)


# Block comments, including the JSX `{/* … */}` form.
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_comments(text: str) -> str:
    """Remove comments, so prose ABOUT a withdrawn figure is not read as the figure.

    The same reasoning ``no-colour-literals`` gives for stripping comments before counting a
    hex value: a size named in a docblock renders nothing, and a check that counts
    documentation penalises writing the decision down. The withdrawal is documented in both
    components and in this file, and none of that prose reaches a screen.

    Line comments are removed only when the line STARTS with ``//`` or ``*``, never when a
    ``//`` appears mid-line. That restriction is load-bearing rather than lazy: a naive
    ``//.*$`` would truncate ``href="https://cdn.example/releases/windows/x.exe"`` at the
    scheme and hide exactly the offender this scan exists to find.
    """
    without_blocks = _BLOCK_COMMENT.sub("", text)
    return "\n".join(
        line
        for line in without_blocks.splitlines()
        if not line.lstrip().startswith(("//", "*"))
    )


def _component_sources():
    """Yield ``(relative_path, code)`` for every component module, comments removed."""
    assert COMPONENTS.is_dir(), f"Missing component tree at {COMPONENTS}"
    for path in sorted(COMPONENTS.rglob("*")):
        if path.suffix in {".js", ".jsx"} and path.is_file():
            relative = path.relative_to(REPO_ROOT).as_posix()
            yield relative, _strip_comments(path.read_text(encoding="utf-8"))


def test_the_comment_stripper_keeps_what_renders():
    """Non-vacuity: a scan with a broken stripper would pass having read nothing.

    Three cases, and the third is the one that would silently gut the scan.
    """
    # Prose about the withdrawn figure: removed.
    assert "84.2 MB" not in _strip_comments("/** advertised at 84.2 MB, withdrawn */")
    assert "84.2 MB" not in _strip_comments("  // 84.2 MB\nconst a = 1;")
    assert "84.2 MB" not in _strip_comments("<div>{/* was 84.2 MB */}</div>")

    # The same figure rendered: kept.
    assert "84.2 MB" in _strip_comments('<div className="x">v0.1.0 · 84.2 MB</div>')

    # A URL with a mid-line `//`: kept whole, path and all.
    kept = _strip_comments('<a href="https://cdn.example/releases/windows/x.exe">get</a>')
    assert "releases/windows/x.exe" in kept
    assert RELEASES_PATH.search(kept)


def test_no_component_holds_an_installer_url():
    """A link cannot be rendered from a URL that is not in the tree."""
    offenders = [
        f"{relative}: {match.group(0)}"
        for relative, text in _component_sources()
        for match in [RELEASES_PATH.search(text)]
        if match
    ]

    assert not offenders, (
        "These components still name a releases/ artifact path. All four return 403, and a "
        "rendered link to one is an advertisement for a file this site does not serve. The "
        "platform cards read their state from design/pageFields.js instead:\n  "
        + "\n  ".join(offenders)
    )


def test_no_component_advertises_a_hardcoded_artifact_size_or_checksum():
    """A size or a checksum for a file that does not exist is a fabricated figure."""
    offenders = []

    for relative, text in _component_sources():
        for size in WITHDRAWN_SIZES:
            if size in text:
                offenders.append(f"{relative}: size {size}")
        checksum = CHECKSUM_LITERAL.search(text)
        if checksum:
            offenders.append(f"{relative}: checksum {checksum.group(0)[:24]}...")

    assert not offenders, (
        "A hardcoded size or checksum for an unpublished artifact is the same fabrication as "
        "a hardcoded balance, on a rendered surface:\n  " + "\n  ".join(offenders)
    )


def _download_declaration() -> str:
    """The `DOWNLOAD_FIELDS` block of design/pageFields.js."""
    assert PAGE_FIELDS.is_file(), f"Missing declaration at {PAGE_FIELDS}"
    text = PAGE_FIELDS.read_text(encoding="utf-8")
    start = text.index("const DOWNLOAD_FIELDS = [")
    end = text.index("\n];", start)
    return text[start:end]


def test_every_withdrawn_platform_is_declared_unavailable_with_a_reason():
    """The surface renders what this block declares, so the block is what has to be right.

    The rendered assertions live in downloadSurface.test.jsx; this one holds the declaration
    itself to four entries, so a platform cannot be quietly dropped from it and leave a card
    with nothing to say.
    """
    block = _download_declaration()

    for platform in WITHDRAWN_PLATFORMS:
        assert f"field: '{platform}'" in block, (
            f"{platform} has no entry in DOWNLOAD_FIELDS; its card would have no declared "
            "reason to render"
        )

    assert block.count("verdict: VERDICT.UNAVAILABLE") == len(WITHDRAWN_PLATFORMS)
    assert block.count("absence: ABSENCE.UNREPORTED") == len(WITHDRAWN_PLATFORMS)
    assert block.count("reason:") == len(WITHDRAWN_PLATFORMS)
    # No entry names a path: these are not values on a response, and a page that read one
    # would be reading a URL to render.
    assert "path:" not in block
