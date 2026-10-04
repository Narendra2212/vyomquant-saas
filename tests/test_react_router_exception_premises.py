"""
tests/test_react_router_exception_premises.py

``react-router`` carries the only npm RUNTIME finding in `.dependabot_raw.jsonl`, and the
exception for it rests on six facts about this frontend. This file pins all six.

WHY A GUARD RATHER THAN A BUMP
``.kiro/specs/production-launch-hardening/dependency-triage.md`` sets the standard
(section "Per task 10.5"): every remaining critical and high on a RUNTIME dependency must
be resolved or carry a checked-in justified exception. Of the eight npm findings in the
dump, seven are ``scope=development`` - ``shell-quote`` (critical), ``brace-expansion``,
``browserslist``, ``@vitest/mocker``, ``baseline-browser-mapping``, ``vitest`` - and one is
runtime: ``react-router``, HIGH.

It is installed at 7.18.4, transitively from ``react-router-dom: ^7.18.4``, and there is no
newer release that closes the open advisory (see the maintainer discussion on
GHSA-qwww-vcr4-c8h2: ``npm audit fix`` cannot resolve it because no newer version exists).
So "bump it" is not available, and the honest answer is a reachability verdict - the same
form section 6 takes for ``cryptography``, where none of three findings has a reachable
call site.

WHAT THE ADVISORIES REQUIRE, AND WHAT THIS APP IS
The react-router 7.x advisory cluster is scoped to server-side execution modes:

  CVE-2026-42342 / GHSA-8x6r-g9mw-2r78   DoS via unbounded path expansion in the
                                         ``__manifest`` endpoint - "React Router Framework
                                         Mode applications (as well as Remix v2)"
  CVE-2026-42211 / GHSA-49rj-9fvp-4h2h   arbitrary constructor invocation via vendored
                                         turbo-stream - "using React Router v7 in
                                         Framework Mode"
  GHSA-qwww-vcr4-c8h2                    CSRF bypass in RSC Mode
  CVE-2026-21884 / GHSA-8v8x-cx79-35w7   XSS in ``<ScrollRestoration>``'s
                                         ``getKey``/``storageKey`` during Server-Side
                                         Rendering
  CVE-2026-53666                         arbitrary constructor injection via
                                         ``deserializeErrors()`` in SSR hydration
  GHSA-chx6-hx7r-mcp5                    a consequence of CVE-2026-42342 which, in the
                                         advisory's own words, does not affect
                                         applications using declarative mode
                                         (``<BrowserRouter>``) or data mode
                                         (``createBrowserRouter``/``<RouterProvider>``)

Content was rephrased for compliance with licensing restrictions. Sources:
https://github.com/advisories/GHSA-8x6r-g9mw-2r78,
https://github.com/advisories/GHSA-49rj-9fvp-4h2h,
https://github.com/advisories/GHSA-8v8x-cx79-35w7,
https://advisories.gitlab.com/npm/react-router/GHSA-qwww-vcr4-c8h2/,
https://advisories.gitlab.com/npm/react-router/CVE-2026-53666/,
https://psirt.com/de/advisory/GHSA-chx6-hx7r-mcp5,
https://github.com/remix-run/remix/discussions/11647

``algo22-terminal`` is a client-rendered Vite single-page application. It has no
``react-router.config.*``, no ``@react-router/dev`` and no ``@remix-run/*`` - so Framework
Mode is not configured and cannot be. It builds with ``vite build`` to static assets and
mounts with ``ReactDOM.createRoot``, not ``hydrateRoot`` - so nothing is server-rendered
and nothing is hydrated. It routes with ``<BrowserRouter>`` - declarative mode, the mode
GHSA-chx6-hx7r-mcp5 names as unaffected. It never renders ``<ScrollRestoration>``. There is
no ``__manifest`` endpoint because that is a Framework Mode server route.

So every advisory in the cluster needs a server-side mode this application does not have.

WHAT WOULD INVALIDATE THE EXCEPTION
Adopting Framework Mode, adding SSR, switching to ``hydrateRoot``, or rendering
``<ScrollRestoration>``. Each of those is a premise below, so the exception fails loudly
rather than being inherited after the thing it depended on changed. That is the whole
point: a justified exception that nothing re-checks is indistinguishable from an ignored
alert.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
FRONTEND = REPO_ROOT / "algo22-terminal"
PACKAGE_JSON = FRONTEND / "package.json"
MAIN = FRONTEND / "src" / "main.jsx"

pytestmark = pytest.mark.skipif(
    not PACKAGE_JSON.is_file(), reason="the frontend is not present in this checkout"
)


def _manifest() -> dict:
    return json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))


def _all_dependencies() -> dict:
    manifest = _manifest()
    merged = {}
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        merged.update(manifest.get(key) or {})
    return merged


def _source_files():
    source = FRONTEND / "src"
    if not source.is_dir():
        return
    for path in source.rglob("*"):
        if path.suffix in {".js", ".jsx", ".ts", ".tsx"} and "__tests__" not in path.parts:
            yield path


# ---------------------------------------------------------------------------
# 1. Framework Mode is not configured, and cannot be
# ---------------------------------------------------------------------------


def test_there_is_no_framework_mode_config():
    """``react-router.config.*`` is what turns on Framework Mode."""
    found = [
        path.name
        for path in FRONTEND.glob("react-router.config.*")
    ]

    assert not found, (
        f"{found} configures React Router Framework Mode. The HIGH advisory exception in "
        f"dependency-triage.md section 8 rests on Framework Mode being absent - "
        f"CVE-2026-42342's `__manifest` DoS and CVE-2026-42211's constructor injection "
        f"both require it. Re-read that section before shipping this."
    )


def test_the_framework_mode_packages_are_not_dependencies():
    """``@react-router/dev`` and ``@remix-run/*`` are how Framework Mode arrives."""
    dependencies = _all_dependencies()
    offenders = [
        name
        for name in dependencies
        if name.startswith("@react-router/") or name.startswith("@remix-run/")
    ]

    assert not offenders, (
        f"{offenders} brings React Router Framework Mode or Remix into this app, which "
        f"invalidates the exception in dependency-triage.md section 8"
    )


# ---------------------------------------------------------------------------
# 2. Nothing is server-rendered
# ---------------------------------------------------------------------------


def test_the_build_produces_static_assets():
    """``vite build`` and nothing else - no SSR build step."""
    scripts = _manifest().get("scripts") or {}
    build = scripts.get("build", "")

    assert "vite build" in build, (
        f"the build script is now {build!r}. The exception assumes a static SPA build; an "
        f"SSR build brings CVE-2026-53666 (SSR hydration) and CVE-2026-21884 (SSR "
        f"ScrollRestoration) into scope."
    )
    assert "react-router build" not in build, "this is a Framework Mode build"


def test_the_app_is_client_rendered_not_hydrated():
    """``createRoot`` mounts a client tree; ``hydrateRoot`` adopts a server-rendered one.

    CVE-2026-53666 is an SSR-hydration defect, so which of the two this app calls is the
    difference between reachable and not.
    """
    if not MAIN.is_file():
        pytest.skip("src/main.jsx is not present in this checkout")
    source = MAIN.read_text(encoding="utf-8")

    assert "createRoot" in source, "src/main.jsx no longer mounts with createRoot"
    assert "hydrateRoot" not in source, (
        "src/main.jsx now hydrates a server-rendered tree, which brings CVE-2026-53666 "
        "(arbitrary constructor injection via deserializeErrors in SSR hydration) into "
        "scope. dependency-triage.md section 8 assumes client rendering."
    )


# ---------------------------------------------------------------------------
# 3. The router is in declarative mode, and ScrollRestoration is unused
# ---------------------------------------------------------------------------


def test_routing_is_declarative_browser_router():
    """The mode GHSA-chx6-hx7r-mcp5 names as unaffected."""
    if not MAIN.is_file():
        pytest.skip("src/main.jsx is not present in this checkout")
    source = MAIN.read_text(encoding="utf-8")

    assert "BrowserRouter" in source, (
        "src/main.jsx no longer routes with <BrowserRouter>. The exception rests on "
        "declarative mode, which GHSA-chx6-hx7r-mcp5 states is unaffected."
    )


def test_scroll_restoration_is_never_rendered():
    """CVE-2026-21884 is an XSS in ``<ScrollRestoration>``'s keys during SSR.

    Asserted across all of ``src/`` rather than only the entry point, because the
    component could be mounted from any route.
    """
    users = [
        f"{path.relative_to(REPO_ROOT).as_posix()}"
        for path in _source_files()
        if re.search(r"\bScrollRestoration\b", path.read_text(encoding="utf-8", errors="replace"))
    ]

    assert not users, (
        f"{users} render <ScrollRestoration>, which CVE-2026-21884 targets through its "
        f"getKey/storageKey props. That advisory needs SSR as well, so this is not "
        f"immediately exploitable - but it removes one of the two conditions the "
        f"exception rests on, so re-read dependency-triage.md section 8."
    )


# ---------------------------------------------------------------------------
# 4. Non-vacuity
# ---------------------------------------------------------------------------


def test_react_router_is_actually_a_dependency():
    """So the assertions above are about a package this app really ships.

    If ``react-router-dom`` were ever dropped, every test here would pass trivially and
    the file would look like a guard while guarding nothing. The exception would also be
    moot - but it should be deleted deliberately, not left passing by absence.
    """
    dependencies = _all_dependencies()

    assert "react-router-dom" in dependencies, (
        "react-router-dom is no longer a dependency, so this guard has nothing to guard. "
        "Delete this file and dependency-triage.md section 8 together."
    )
