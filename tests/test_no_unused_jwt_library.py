"""
tests/test_no_unused_jwt_library.py

``python-jose`` was a declared runtime dependency that nothing imported, and it carried
four CRITICAL advisories.

This is the follow-up
``.kiro/specs/production-launch-hardening/dependency-triage.md`` names and defers:

    **Follow-up, not done here (out of scope):** the stronger fix is to drop the
    `python-jose[cryptography]` pin entirely, since it is unused and only adds attack
    surface and image weight. That is a manifest removal with an import-audit check
    attached and is deliberately left for a separate change.

This file is that import-audit check, and the removal is the change.

WHY REMOVING BEATS PINNING
The pin had already been moved 3.3.0 -> 3.4.0 to clear CVE-2024-33663 (algorithm
confusion, CWE-347). That was correct and free, but it only answers the advisories that
existed on the day it was taken. A dependency that is installed and never imported goes on
generating advisories forever, each one needing its own triage, for a library the product
does not use. Deleting it answers all of them at once and cannot regress a call site,
because there is no call site.

THE EVIDENCE, RE-TAKEN RATHER THAN INHERITED
Three independent facts, each asserted below so none of them can rot:

  * No module imports it. A repo-wide parse finds no ``import jose`` and no
    ``from jose ...`` outside vendored trees.
  * No installed distribution requires it. ``importlib.metadata`` over every installed
    distribution's ``requires`` finds nothing naming it, so it was a direct pin rather
    than something pulled in underneath another package.
  * The JWT work is PyJWT's. ``core/auth_middleware.py`` imports ``jwt``,
    ``PyJWKClient`` and PyJWT's exception types, and ``decode_token_local`` pins
    ``algorithms=["ES256"]`` against a JWKS-resolved key rather than selecting one from
    the token's own unverified ``alg`` header - which is the shape CVE-2024-33663
    exploits, and which this path never had.

So the honest statement is the one the triage doc makes: CVE-2024-33663 had no reachable
call site in this image even at 3.3.0. Removal is not a security fix for an open hole, it
is the deletion of a permanent source of alerts that can never be exploited because
nothing calls it.

WHAT WOULD MAKE THIS FILE FAIL, AND WHAT TO DO ABOUT IT
If someone adds ``from jose import jwt``, ``test_nothing_imports_jose`` fails. That is the
intended outcome: the choice is PyJWT, which the auth path already uses and already
constrains to ES256, and adding a second JWT implementation means two algorithm policies
to keep in agreement. If a genuine need for python-jose appears, re-adding the pin and
deleting this file is the honest way to do it - not re-adding the pin and leaving an
import-audit that no longer audits anything.
"""

from __future__ import annotations

import ast
import os
import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Vendored or duplicated trees and build artefacts. ``connection_layer/`` and
#: ``aerora_quant_platform/`` carry their own copies of the backend, so a match inside
#: them says nothing about what this image installs.
SKIP_DIRS = {
    ".venv", ".codex_verify_venv", ".git", "node_modules", ".hypothesis", "__pycache__",
    ".pytest_cache", ".ruff_cache", ".mypy_cache", "dist", "build",
    "python_portable", "Git", "connection_layer", "aerora_quant_platform",
}

#: Every requirements manifest the image build reads.
REQUIREMENT_FILES = (
    "requirements.txt",
    "requirements-base.txt",
    "requirements-cpu.txt",
    "requirements-dev.txt",
)

#: The distribution name, and the module name it installs. They differ, which is why both
#: are checked: the manifest says ``python-jose`` and the import says ``jose``.
DISTRIBUTION = "python-jose"
MODULE = "jose"


def _requirement_lines():
    """``[(filename, line)]`` for every non-comment, non-flag requirement line."""
    for name in REQUIREMENT_FILES:
        path = REPO_ROOT / name
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line or line.startswith("-"):
                continue
            yield name, line


def _declared_distributions():
    """``{normalised name: "file: line"}`` for every pinned requirement.

    EXTRAS ARE STRIPPED, and that is load-bearing. The pin read
    ``python-jose[cryptography]==3.4.0``, and a naive ``^[A-Za-z0-9._-]+`` match skips it
    entirely because of the bracket - which is exactly how a first pass at this audit
    reported the package as "not pinned in any requirements file" while the pin was
    sitting in ``requirements-base.txt``. Normalised per PEP 503 so ``python_jose`` and
    ``Python-Jose`` cannot slip past either.
    """
    found = {}
    for name, line in _requirement_lines():
        match = re.match(r"^([A-Za-z0-9._-]+)\s*(?:\[[^\]]*\])?\s*(.*)$", line)
        if not match:
            continue
        normalised = re.sub(r"[-_.]+", "-", match.group(1)).lower()
        found.setdefault(normalised, f"{name}: {line}")
    return found


def _python_files():
    for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for filename in filenames:
            if filename.endswith(".py"):
                yield pathlib.Path(dirpath) / filename


# ---------------------------------------------------------------------------
# 1. It is gone from the manifests
# ---------------------------------------------------------------------------


def test_python_jose_is_not_declared_in_any_requirements_file():
    """The removal itself."""
    declared = _declared_distributions()

    assert DISTRIBUTION not in declared, (
        f"{DISTRIBUTION} is declared again ({declared[DISTRIBUTION]}). It carried four "
        f"CRITICAL advisories and has no import site in this repository; the JWT work is "
        f"PyJWT's. If it is genuinely needed now, delete this file along with re-adding "
        f"the pin rather than leaving an import-audit that no longer audits anything."
    )


def test_the_extras_aware_parse_actually_finds_pins():
    """Non-vacuity, and a guard on the bug that made the first audit wrong.

    A parser that silently matched nothing would make the assertion above pass for any
    manifest at all, including one that still pinned the package. The two names checked
    here are both real pins: ``PyJWT`` is plain, and ``passlib[bcrypt]`` carries extras -
    the shape that defeated the first regex.
    """
    declared = _declared_distributions()

    assert "pyjwt" in declared, f"the requirement parser found no PyJWT: {sorted(declared)[:10]}"
    assert "passlib" in declared, (
        "the requirement parser does not handle extras, so a `python-jose[cryptography]` "
        "pin would be invisible to the assertion above"
    )


# ---------------------------------------------------------------------------
# 2. Nothing imported it, and nothing needed it
# ---------------------------------------------------------------------------


def test_nothing_imports_jose():
    """Parsed, not grepped: a comment or a docstring naming it is not an import.

    This file's own prose mentions ``jose`` repeatedly, and so does
    ``dependency-triage.md``. A text search would match both and report a call site that
    does not exist.
    """
    importers = []
    for path in _python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, OSError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] == MODULE:
                        importers.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
            elif isinstance(node, ast.ImportFrom):
                if (node.module or "").split(".")[0] == MODULE:
                    importers.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")

    assert not importers, (
        f"something imports `{MODULE}` at {importers}, but the distribution that provides "
        f"it is no longer declared - so this would be an ImportError in the built image. "
        f"Use PyJWT, which the auth path already uses and already constrains to ES256."
    )


def test_no_installed_distribution_requires_it():
    """So the pin was direct, and removing it cannot break another package's dependency.

    Skipped rather than failed when the metadata cannot be read: this asserts a property
    of the environment the test happens to run in, and an interpreter that cannot
    enumerate its own distributions is a broken harness, not a defect in the manifest.
    """
    import importlib.metadata as metadata

    try:
        distributions = list(metadata.distributions())
    except Exception as exc:  # noqa: BLE001 - harness problem, not a finding
        pytest.skip(f"installed distributions are not enumerable here: {exc}")

    requiring = []
    for distribution in distributions:
        name = (distribution.metadata or {}).get("Name") or "<unknown>"
        for requirement in distribution.requires or []:
            head = re.split(r"[\s<>=!;\[]", requirement, maxsplit=1)[0]
            if re.sub(r"[-_.]+", "-", head).lower() == DISTRIBUTION:
                requiring.append(f"{name} -> {requirement}")

    assert not requiring, (
        f"{DISTRIBUTION} is required by {requiring}, so it is not a direct pin and "
        f"removing it from the manifest does not remove it from the image"
    )


# ---------------------------------------------------------------------------
# 3. What does the JWT work instead
# ---------------------------------------------------------------------------


def test_pyjwt_is_what_the_auth_path_uses():
    """Asserted positively, so this file says what replaced it rather than only what left.

    ``decode_token_local`` naming ``algorithms=["ES256"]`` is the part that matters: it
    resolves the key from the JWKS rather than trusting the token's own ``alg`` header,
    which is the CWE-347 shape CVE-2024-33663 exploited in python-jose. This path never
    had that exposure.
    """
    middleware = REPO_ROOT / "backend_app" / "core" / "auth_middleware.py"
    if not middleware.is_file():
        pytest.skip("auth_middleware.py is not present in this checkout")

    source = middleware.read_text(encoding="utf-8", errors="replace")
    identifiers = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover - the module is importable in CI
        pytest.skip("auth_middleware.py does not parse in this checkout")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            identifiers.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            identifiers.add((node.module or "").split(".")[0])

    assert "jwt" in identifiers, (
        "auth_middleware no longer imports PyJWT's `jwt`; re-read this file's premise "
        "before trusting it"
    )
    assert '"ES256"' in source or "'ES256'" in source, (
        "auth_middleware no longer pins ES256. The algorithm allow-list is what makes "
        "the key-confusion class unreachable here."
    )
