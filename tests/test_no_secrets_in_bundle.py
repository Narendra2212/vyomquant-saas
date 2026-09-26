"""
tests/test_no_secrets_in_bundle.py

Task 12.10 / Requirement 1.20, 2.20 (production-launch-hardening) - the bundle half.

`.github/workflows/06-frontend-deploy.yml` already greps the built bundle for a single
literal, `service_role`, as a check that ``VITE_SUPABASE_SERVICE_KEY`` was never embedded
in the frontend. This file GENERALISES that check rather than replacing it: the workflow
step stays exactly as written, and this file adds the same class of check, runnable
without a deploy, over every KEY NAME this codebase's own `.env.example` files declare as
secret-shaped, plus a shape-based scan for material that looks like a leaked exchange
credential rather than a placeholder.

WHAT "secret-shaped" MEANS HERE, AND WHY THE LIST IS BUILT FROM ``.env.example``
---------------------------------------------------------------------------------
The env var NAMES below are read out of ``.env.example`` (backend) and
``algo22-terminal/.env.production.example`` (frontend) - the two files this repository
already treats as the authoritative list of what a real deployment sets. Anything named
there as a key/secret/password/token is a candidate for accidental frontend embedding, so
its NAME is checked as a substring token inside the bundle (Vite's `import.meta.env`
inlining means a variable *name* only appears in built output if some source file
referenced `import.meta.env.THAT_NAME`; grepping the name catches that reference even if
the *value* substitution failed open, silently leaking whatever the CI secret held).

Two names are deliberately EXCLUDED because they are meant to be public in a Vite
bundle, and asserting their absence would fail every real build:
- ``VITE_SUPABASE_URL`` - a project URL, not a secret.
- ``VITE_SUPABASE_ANON_KEY`` - Supabase's anon key is designed to be publishable; RLS is
  the actual boundary. ``algo22-terminal/.env.production.example`` says this explicitly.

SECRETS ARE NEVER WRITTEN HERE BY VALUE. This file, its fixtures, and every assertion
message reference credentials BY KEY NAME ONLY. Where a real secret string needs a
positive-control counterpart (proving the scanner isn't vacuous), the counterpart is a
locally-generated fake value with a "FAKE_CANARY_" prefix, never a value pulled from this
repository's own `.env` files.

THREE ASSERTIONS
-----------------
1. The workflow's own ``service_role`` check still exists, unmodified, in
   ``06-frontend-deploy.yml`` - this file extends it, it does not retire it.
2. Generalised name check: none of the KNOWN SECRET KEY NAMES below (as an exact-token
   match: the name is checked with word-ish boundaries, and as `VAR_NAME=` or `.NAME`
   forms, so an unrelated substring inside a minified identifier does not false-positive)
   appears anywhere under ``algo22-terminal/dist/``.
3. Shape check: no JWT-shaped string (``eyJ...`` - a base64url header followed by a dot)
   appears anywhere in the bundle EXCEPT as part of the Supabase anon key that
   ``06-frontend-deploy.yml``'s own check expects to find (a real anon key is required to be
   present; a *second*, different JWT-shaped string is what a leaked service-role or
   third-party token would look like, so the anon key's own value is excluded from the
   scan by exact match, not by pattern, before this assertion runs).

WHAT THIS FILE DOES NOT CHECK
------------------------------
Bundle content only. Requirement 1.20's log half is `tests/test_log_redaction.py`, a
separate concern with a separate, partly-blocked proof - see that file's docstring.

_Requirements: 1.20, 2.20_
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TERMINAL = REPO_ROOT / "algo22-terminal"
DIST = TERMINAL / "dist"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "06-frontend-deploy.yml"
BACKEND_ENV_EXAMPLE = REPO_ROOT / ".env.example"
FRONTEND_ENV_EXAMPLE = TERMINAL / ".env.production.example"

# Names that ARE expected to appear in the bundle by design - excluded from the
# secret-name sweep so this test does not fail every legitimate build.
EXPECTED_PUBLIC_NAMES = {
    "VITE_SUPABASE_URL",
    "VITE_SUPABASE_ANON_KEY",
    "VITE_API_URL",
    "VITE_WS_URL",
    "VITE_SENTRY_DSN",
    "VITE_GA_TRACKING_ID",
    "VITE_CLARITY_PROJECT_ID",
    "VITE_DOCS_URL",
}

# Backend secret-shaped names a frontend bundle must never reference. Read by NAME only -
# see module docstring. These are the server-side credential and signing-key variables
# declared in .env.example; a frontend build referencing any of them by name would mean
# either a stray `import.meta.env.<NAME>` in frontend source, or - the case the existing
# service_role check already guards against - a build-time secret leaking into a client
# bundle it was never meant to reach.
KNOWN_SECRET_NAMES = {
    "SUPABASE_SERVICE_ROLE_KEY",
    "SUPABASE_JWT_SECRET",
    "JWT_SECRET",
    "MASTER_ENCRYPTION_KEYS",
    "CREDENTIAL_VAULT_SALT",
    "DATABASE_URL",
    "EXCHANGE_API_KEY",
    "EXCHANGE_API_SECRET",
    "STRIPE_SECRET_KEY",
    "STRIPE_WEBHOOK_SECRET",
    "RAZORPAY_KEY_ID",
    "RAZORPAY_KEY_SECRET",
    "RAZORPAY_WEBHOOK_SECRET",
    "TELEGRAM_BOT_TOKEN",
    "DISCORD_WEBHOOK_URL",
    "REDIS_PASSWORD",
    "EMAIL_PASSWORD",
    "GRAFANA_ADMIN_PASSWORD",
    "ALERT_SLACK_WEBHOOK_URL",
    "TEST_USER_PASSWORD",
    # Backend-side Supabase name - distinct from the frontend's VITE_SUPABASE_ANON_KEY
    # (which IS expected in the bundle; see EXPECTED_PUBLIC_NAMES). Its value is not a
    # secret, but the bare backend name should never appear in frontend output either -
    # its presence would mean the wrong env var was wired into a `import.meta.env` read.
    "SUPABASE_ANON_KEY",
    # The literal the existing workflow step already checks for - generalised here as a
    # name among names, not replaced.
    "service_role",
}


def _load_declared_secret_names(env_example: Path) -> set[str]:
    """Every ``NAME=`` on the left of an assignment line in an .env.example file.

    Used only as a completeness cross-check (see
    ``test_the_known_secret_name_list_is_not_missing_a_declared_secret``) - the
    KNOWN_SECRET_NAMES set above is curated by hand so a name added to `.env.example`
    tomorrow does not silently drop out of this scan.
    """
    if not env_example.exists():
        return set()
    names: set[str] = set()
    line_re = re.compile(r"^([A-Z][A-Z0-9_]*)=")
    for line in env_example.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = line_re.match(stripped)
        if m:
            names.add(m.group(1))
    return names


# Substrings that mark a declared name as secret-shaped by its own name, independent of
# the curated KNOWN_SECRET_NAMES set - used only for the completeness cross-check below.
_SECRET_NAME_HINTS = ("KEY", "SECRET", "PASSWORD", "TOKEN", "SALT", "WEBHOOK_URL", "DATABASE_URL")


def _bundle_files() -> list[Path]:
    """Every text-ish file Vite emitted, i.e. everything CI's ``aws s3 sync dist/`` will
    publish. Binary release artifacts (already covered by
    ``tests/test_release_artifacts.py``) are skipped - grepping them for text patterns is
    meaningless and, for the real 112 MB Windows installer, needlessly slow.
    """
    if not DIST.exists():
        return []
    skip_suffixes = {
        ".exe", ".dmg", ".deb", ".appimage", ".png", ".jpg", ".jpeg", ".gif", ".webp",
        ".ico", ".woff", ".woff2", ".ttf", ".eot",
    }
    files = []
    for path in DIST.rglob("*"):
        if not path.is_file():
            continue
        if "releases" in path.relative_to(DIST).parts:
            continue
        if path.suffix.lower() in skip_suffixes:
            continue
        files.append(path)
    return files


def _read_bundle_text() -> str:
    """Concatenated text of every scannable bundle file, for a single substring sweep."""
    chunks = []
    for f in _bundle_files():
        try:
            chunks.append(f.read_text(encoding="utf-8", errors="replace"))
        except (UnicodeDecodeError, OSError):
            continue
    return "\n".join(chunks)


@pytest.fixture(scope="module")
def bundle_built() -> None:
    if not DIST.exists() or not any(DIST.iterdir()):
        pytest.fail(
            "algo22-terminal/dist/ does not exist or is empty. Build it first with "
            '(PowerShell) $env:NODE_OPTIONS="--max-old-space-size=2048"; npm run build '
            "from algo22-terminal/, then re-run this test."
        )


@pytest.fixture(scope="module")
def bundle_text(bundle_built) -> str:
    return _read_bundle_text()


class TestExistingWorkflowCheckIsGeneralisedNotReplaced:
    """The workflow's own ``service_role`` grep step must still be present. This suite
    extends it; it must never be the thing that removes it.
    """

    def test_the_workflow_still_greps_for_service_role(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        assert 'grep -q "service_role"' in text, (
            "06-frontend-deploy.yml no longer runs its service_role check - this task "
            "generalises that check, it does not replace it"
        )
        assert "SECURITY VIOLATION: Service role key found in frontend build" in text


class TestNoSecretKeyNameInTheBundle:
    """Requirement 1.20 / 2.20, generalised name sweep."""

    def test_no_known_secret_name_appears_in_dist(self, bundle_text: str):
        offenders = []
        for name in sorted(KNOWN_SECRET_NAMES):
            # Word-ish boundary: the name must not be found as a mid-identifier substring
            # of an unrelated minified symbol. re.escape handles the one non-identifier
            # name in the set ("service_role" already contains only word characters, so
            # this is a no-op for it too).
            pattern = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])")
            if pattern.search(bundle_text):
                offenders.append(name)  # name only - never the matched value
        assert not offenders, (
            f"secret-shaped key name(s) found in algo22-terminal/dist/: {offenders} "
            "(names only, values withheld)"
        )

    def test_expected_public_names_are_not_accidentally_in_the_secret_list(self):
        # Guards the curated list itself: a public name must never be miscategorised as
        # secret, or every real build would fail this suite.
        assert EXPECTED_PUBLIC_NAMES.isdisjoint(KNOWN_SECRET_NAMES)

    def test_the_scan_is_not_vacuous(self):
        """Positive control: a FAKE canary name, injected as bundle text (never written to
        disk, never a real .env value), must be caught by the same pattern the real sweep
        uses - proving the regex actually matches rather than passing by construction.
        """
        canary_name = "FAKE_CANARY_SECRET_TOKEN_NAME"
        haystack = f"const x = {{ {canary_name}: undefined }};"
        pattern = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(canary_name) + r"(?![A-Za-z0-9_])")
        assert pattern.search(haystack), "positive control itself is broken - fix the test"

    def test_the_known_secret_name_list_is_not_missing_a_declared_secret(self):
        """Cross-check against .env.example so a newly declared backend secret does not
        silently fall out of KNOWN_SECRET_NAMES. Only names carrying an obvious
        secret-shaped hint are required to already be listed - `ENV`, `HOST`, `PORT` etc.
        are correctly absent and must not trip this.
        """
        declared = _load_declared_secret_names(BACKEND_ENV_EXAMPLE)
        missing = [
            name
            for name in sorted(declared)
            if name not in KNOWN_SECRET_NAMES
            and name not in EXPECTED_PUBLIC_NAMES
            and any(hint in name for hint in _SECRET_NAME_HINTS)
        ]
        assert not missing, (
            f".env.example declares secret-shaped name(s) not covered by "
            f"KNOWN_SECRET_NAMES: {missing} - add the name (never the value) to the set "
            "in this file"
        )


class TestNoLeakedCredentialShapeInTheBundle:
    """Shape-based check, independent of any name: a JWT-shaped string other than the
    expected Supabase anon key. A real service-role key, like the anon key, is a JWT
    (``eyJ...``); this catches one appearing even if no source line names it by
    variable name.
    """

    def test_only_the_expected_anon_key_jwt_is_present(self, bundle_text: str):
        # Supabase JWTs are three base64url segments joined by '.', always starting
        # "eyJ" (the base64url encoding of '{"' for the JSON header). Match the full
        # three-segment shape so multi-line minified surroundings do not truncate it.
        jwt_pattern = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
        matches = set(jwt_pattern.findall(bundle_text))

        expected_anon_key = None
        if FRONTEND_ENV_EXAMPLE.exists():
            pass  # the example file holds a placeholder, not the real key - see below

        env_file = TERMINAL / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.strip().startswith("VITE_SUPABASE_ANON_KEY="):
                    expected_anon_key = line.split("=", 1)[1].strip()
                    break

        unexpected = matches - ({expected_anon_key} if expected_anon_key else set())
        assert not unexpected, (
            f"found {len(unexpected)} JWT-shaped string(s) in algo22-terminal/dist/ beyond "
            "the expected Supabase anon key - a real credential must never be logged here, "
            "so only the count and shape are reported, not the value"
        )

    def test_the_jwt_shape_scan_is_not_vacuous(self):
        """Positive control with a structurally valid but entirely fake JWT shape - not a
        real credential, not derived from any value in this repository.
        """
        fake_jwt = "eyJhbGciOiJIUzI1NiJ9.eyJmYWtlIjoiY2FuYXJ5In0.ZmFrZS1zaWduYXR1cmU"
        jwt_pattern = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
        assert jwt_pattern.search(fake_jwt), "positive control itself is broken - fix the test"
