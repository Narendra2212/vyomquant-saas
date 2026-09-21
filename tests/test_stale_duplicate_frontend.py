"""Task 4.8 regression test: no build or deploy path references the stale duplicate frontend.

A second frontend tree sits at ``aerora_quant_platform/frontend_app/algo22-terminal``. It is
stale, and the hazard 1.35 names is not that it is wrong — it is that it is **indistinguishable
by path convention from the live one**. Both end in ``algo22-terminal``, both hold a
``src/components/``, both hold a ``vite.config.js``. A workflow, a Dockerfile or a deploy
script that names the wrong one builds bytes nobody reviewed, and nothing in the pipeline would
report that as an error.

WHY THIS IS A CHECK AND NOT A DELETION
--------------------------------------
Requirement 3.16 keeps the tree unmodified, so the fix cannot be to edit or remove it. What
CAN be fixed is the ambiguity: the set of build and deploy paths that reference it is asserted
to be empty, so if one ever starts to, the assertion names it. Marking the duplicate dead means
making the reference set decidable, not editing the duplicate.

WHAT IS SCANNED, AND WHAT IS DELIBERATELY NOT
---------------------------------------------
Scanned: ``.github/workflows/**``, every ``package.json``, every ``vite.config.*``, every
``Dockerfile*``, ``infra/**`` and ``scripts/**``. Those are the paths that turn source into a
deployed artifact.

Not scanned, each for a reason:

* **Anything under ``aerora_quant_platform/`` itself.** Its own ``package.json``,
  ``vite.config.js`` and ``Dockerfile.frontend`` describe the stale tree to itself. A
  self-reference there is not a live build path, and 3.16 forbids editing it in any case, so
  including it would produce a failure with no legal remedy.
* **``.gitignore`` and ``.dockerignore``.** Both name ``aerora_quant_platform/``, and in both
  a match is the EXCLUSION doing its job — the desired state, not the defect. Scanning them
  would invert the meaning of a hit.

Two prose references exist outside this scan and are recorded here rather than left invisible:
``deployment_go_no_go.md`` step 6 reads ``cd aerora_quant_platform/frontend_app && vercel
--prod``, and ``docs/history/DOMAIN_DEPLOYMENT_PLAN.md`` and
``docs/history/PROJECT_ARCHITECTURE.md`` describe the tree as a parallel codebase. Those are a
runbook and two history documents, not build paths, and neither is in task 4.8's file list.
They are the reason the assertion below also rejects the PARENT ``aerora_quant_platform/
frontend_app`` prefix: a pipeline step that built the parent would reach the duplicate's
sibling, which is the same hazard one directory up.

_Requirements: 1.35, 2.35, 3.16_
"""

from __future__ import annotations

import os
import re
import subprocess
from fnmatch import fnmatch
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The duplicate itself, and the parent a build step could plausibly name instead.
DUPLICATE_PATH = "aerora_quant_platform/frontend_app/algo22-terminal"
DUPLICATE_PARENT = "aerora_quant_platform/frontend_app"

#: Either path, in either slash convention.
DUPLICATE_REFERENCE = re.compile(
    r"aerora_quant_platform[/\\]frontend_app(?:[/\\]algo22-terminal)?",
)

#: Directories no scan descends into. Dependency, build-output and tooling trees, plus the
#: stale tree itself. Anything whose name starts with a dot is pruned as well, which covers
#: ``.git``, ``.hypothesis`` and the throwaway virtualenvs without listing them.
SKIP_DIRECTORIES = frozenset(
    {
        "aerora_quant_platform",  # the stale tree; see the module docstring
        "node_modules",
        "__pycache__",
        "dist",
        "build",
        "target",  # src-tauri's Rust build output
        "coverage",
        "releases",
        "venv",
    }
)


def _pruned(dirnames: list[str]) -> list[str]:
    """The subdirectories a walk continues into."""
    return [name for name in dirnames if name not in SKIP_DIRECTORIES and not name.startswith(".")]


def _walk(root: Path):
    """Every file below ``root``, pruning excluded directories DURING the walk.

    Pruning in place rather than filtering afterwards is what keeps this a second rather than
    a two-minute scan: ``rglob`` descends into ``node_modules`` and the tooling caches before
    anything gets a chance to discard them.
    """
    if not root.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = _pruned(dirnames)
        for name in filenames:
            yield Path(dirpath) / name


@lru_cache(maxsize=1)
def _repo_files() -> tuple[Path, ...]:
    """One pruned walk of the repository, shared by every name-pattern category.

    Cached because three of the tests below read the same file set, and the walk is the only
    expensive thing in this module. Note that this omits dot-directories by construction, so
    ``.github/workflows`` is collected by ``_files_under`` from its own root instead.
    """
    return tuple(_walk(REPO_ROOT))


def _files_matching(pattern: str) -> list[Path]:
    """Every file below the repo root whose NAME matches ``pattern`` (``fnmatch`` syntax)."""
    return sorted(path for path in _repo_files() if fnmatch(path.name, pattern))


def _files_under(*relative_roots: str) -> list[Path]:
    """Every file below each of ``relative_roots``."""
    found: list[Path] = []
    for relative in relative_roots:
        found.extend(_walk(REPO_ROOT / relative))
    return sorted(found)


@lru_cache(maxsize=1)
def _build_and_deploy_paths() -> dict[str, tuple[Path, ...]]:
    """The six categories task 4.8 names, keyed so a failure says which kind of path failed."""
    return {
        "workflows": tuple(_files_under(".github/workflows")),
        "package.json": tuple(_files_matching("package.json")),
        "vite config": tuple(_files_matching("vite.config.*")),
        "Dockerfile": tuple(_files_matching("Dockerfile*")),
        "infra": tuple(_files_under("infra")),
        "scripts": tuple(_files_under("scripts")),
    }


def _read(path: Path) -> str:
    """Text of ``path``, tolerant of encoding: this scan looks for one ASCII substring."""
    return path.read_text(encoding="utf-8", errors="replace")


def test_the_duplicate_frontend_still_exists_and_is_uncommitted():
    """The premise. Without it every assertion below would pass vacuously.

    The tree is gitignored, so ``git diff`` over it is silent by construction — the 3.16 claim
    that has content is that NOTHING under it is tracked, which is what keeps a change to it
    out of a commit whether or not anyone remembers to look.
    """
    duplicate = REPO_ROOT / DUPLICATE_PATH
    if not duplicate.is_dir():
        # Removal is a legitimate future state and is not a failure of this check: with the
        # tree gone there is nothing to reference and nothing to keep unmodified.
        return

    tracked = subprocess.run(
        ["git", "ls-files", "--", DUPLICATE_PATH],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert tracked.stdout.strip() == "", (
        "Files under the stale duplicate frontend are tracked by git. Requirement 3.16 leaves "
        "that tree unmodified, and a tracked file there can enter a commit:\n  "
        + "\n  ".join(tracked.stdout.split())
    )


def test_the_scan_reaches_every_category_of_build_path():
    """Non-vacuity. A scan that collected nothing would report zero references honestly."""
    paths = _build_and_deploy_paths()
    empty = [category for category, found in paths.items() if not found]

    assert not empty, (
        "These categories of build path collected no files, so the reference assertion below "
        f"would pass without reading anything: {empty}"
    )
    # The live frontend's own config must be among them, or the scan is looking at the wrong
    # tree — the one thing a path-convention collision makes easy to get wrong.
    vite_configs = {p.relative_to(REPO_ROOT).as_posix() for p in paths["vite config"]}
    assert "algo22-terminal/vite.config.js" in vite_configs
    assert f"{DUPLICATE_PARENT}/vite.config.js" not in vite_configs


def test_the_reference_pattern_matches_what_it_claims_to():
    """The pattern, exercised against a planted reference in each form it could take."""
    for planted in (
        "cd aerora_quant_platform/frontend_app/algo22-terminal && npm ci",
        "COPY aerora_quant_platform\\frontend_app\\algo22-terminal .",
        "working-directory: aerora_quant_platform/frontend_app",
    ):
        assert DUPLICATE_REFERENCE.search(planted), planted

    # And not against the live tree, which shares the last path segment. This is the whole
    # point of the defect: the two are distinguishable only by what comes before it.
    assert not DUPLICATE_REFERENCE.search("working-directory: algo22-terminal")
    assert not DUPLICATE_REFERENCE.search("cd algo22-terminal && npm run build")


def test_no_build_or_deploy_path_references_the_duplicate_frontend():
    """The assertion itself: the reference set is empty, and stays that way."""
    offenders = []

    for category, paths in _build_and_deploy_paths().items():
        for path in paths:
            for number, line in enumerate(_read(path).splitlines(), start=1):
                if DUPLICATE_REFERENCE.search(line):
                    offenders.append(
                        f"{category}: {path.relative_to(REPO_ROOT).as_posix()}:{number}: "
                        f"{line.strip()}"
                    )

    assert not offenders, (
        f"A build or deploy path names the stale duplicate frontend at {DUPLICATE_PATH}. It is "
        "indistinguishable by path convention from the live frontend at algo22-terminal/, so a "
        "step that builds it publishes bytes nobody reviewed. Point the step at "
        "algo22-terminal/ — the duplicate is not modified (Requirement 3.16):\n  "
        + "\n  ".join(offenders)
    )
