"""A `vi.mock` of `src/api` that omits `isAuthenticated` fails the RUN, not a test.

THE DEFECT, THREE TIMES
-----------------------
`src/hooks/useEntitlements.js` does `import { api, isAuthenticated } from '../api'` and opens
its `load()` with `if (!isAuthenticated())` (line 99). A `vi.mock` factory replaces the WHOLE
module, so an export the factory omits does NOT fall through to the real one -- touching it
throws `[vitest] No "isAuthenticated" export is defined on the "..." mock`.

It throws inside an async `load`, which means it never reaches an assertion. It lands as an
UNHANDLED ERROR, and vitest exits non-zero on those while still reporting every test as
passing. All three times it has happened, the report read `Test Files 155 passed (155)` and
the job was red:

    tests/unit/pages/strategyMarketplace.test.jsx     57 unhandled rejections
    src/pages/__tests__/StrategyMarketplace.test.jsx   1 unhandled error
    tests/unit/pages/degradedSurfaces.test.jsx         6 unhandled errors

Each was found by a CI run, one at a time, because a file only surfaces it if something it
renders reaches `useEntitlements`. Fixing them as they appear is whack-a-mole with a 5-minute
feedback loop, so this is the ratchet instead.

WHAT THIS IS, AND WHY IT IS A RATCHET AND NOT A SWEEP
------------------------------------------------------
Around twenty frontend test files mock `src/api` with a factory that omits the export. Most
are harmless today: they render nothing that reads entitlements. Adding the export to all of
them would be a twenty-file diff asserting nothing, and in several the right VALUE is not
obvious -- `() => false` versus reading `sessionStorage.token` changes what the test measures,
and picking wrong makes a mock that disagrees with its own fixture.

So the current set is PINNED as a declared, shrinking list:

  * a file NOT in the list that omits `isAuthenticated` FAILS -- no new incomplete factory.
  * a file IN the list that now provides it ALSO FAILS -- the list must shrink as files are
    fixed, so it cannot quietly go stale and start grandfathering things nobody checked.

The second rule is the half that matters. A plain allowlist rots; one that rejects its own
obsolete entries cannot.

HOW TO FIX A FILE RATHER THAN ADD IT HERE. Decide what the test means by "signed in". If it
drives `sessionStorage.setItem('token', ...)`, answer from that store --
`isAuthenticated: () => !!sessionStorage.getItem('token')`, as
`tests/unit/lib/singleSocket.test.jsx`, `degradedSurfaces.test.jsx` and
`src/pages/__tests__/StrategyMarketplace.test.jsx` do. If it never signs in, `() => false` is
honest, as `tests/unit/pages/strategyMarketplace.test.jsx` does. If the point of the test is
that the app asks the HTTP client, import the real helper, as
`tests/unit/pages/billingSocketLifecycle.test.jsx` does. Then delete the file from the list
below -- this test will tell you to.
"""

from __future__ import annotations

import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = REPO_ROOT / "algo22-terminal"

#: The export every factory for this module owes, because `useEntitlements` imports it.
REQUIRED_EXPORT = "isAuthenticated"

#: `vi.mock('<anything>/api', ...)` or `vi.mock('api', ...)`. The final path segment must be
#: exactly `api`, so `src/apiClient` -- a different module with a different surface -- does
#: not match.
VI_MOCK_API = re.compile(r"""vi\.mock\(\s*['"]((?:[^'"]*/)?api)['"]""")

#: Files whose `src/api` factory omits `isAuthenticated` as of the commit that added this
#: test. Paths are relative to `algo22-terminal/`. SHRINK THIS LIST; do not add to it.
#:
#: None of these currently renders a component that reaches `useEntitlements`, which is the
#: only reason they are green. That is a property of what they render today, not a guarantee,
#: and it is exactly how the three known instances arrived.
KNOWN_INCOMPLETE_API_MOCKS = frozenset(
    {
        "tests/unit/backtestResultTiers.test.jsx",
        "tests/unit/backtestSaveFlow.test.jsx",
        "tests/unit/backtestStrategySelection.test.jsx",
        "tests/unit/deploy/deployConfirmation.test.jsx",
        "tests/unit/deployPreflight.test.jsx",
        "tests/unit/exchange_manager_phase6.test.jsx",
        "tests/unit/pages/riskSettingsKillSwitch.test.jsx",
        "tests/unit/shell/accountMenu.test.jsx",
        "tests/unit/shell/topBar.test.jsx",
        "tests/unit/shell/useUnreadNotifications.test.jsx",
        "tests/unit/signalTraceConnection.test.jsx",
        "tests/unit/signalTraceDedup.test.jsx",
        "tests/unit/signalTraceRealtime.test.jsx",
        "tests/unit/strategies-table.test.jsx",
        "tests/unit/strategyArchive.test.jsx",
        "tests/unit/builder/enginePaths.test.jsx",
        "src/pages/__tests__/PaperTrading.test.jsx",
        "src/pages/__tests__/Strategies.test.jsx",
    }
)


def _test_files() -> list[pathlib.Path]:
    return sorted(
        p
        for p in FRONTEND.rglob("*.test.jsx")
        if "node_modules" not in p.parts and "dist" not in p.parts
    )


def _call_span(source: str, open_paren_index: int) -> str:
    """The text of a call expression, from its opening paren to the matching close.

    Brace/paren balanced rather than regex-matched, because a `vi.mock` factory is an
    arbitrary arrow function body and regex cannot find its end. Quotes are tracked so a
    paren inside a string literal does not shift the balance.
    """
    depth = 0
    quote: str | None = None
    escaped = False
    for i in range(open_paren_index, len(source)):
        ch = source[i]
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            continue
        if ch in "'\"`":
            quote = ch
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                return source[open_paren_index : i + 1]
    return source[open_paren_index:]


def _api_mock_factories(source: str) -> list[str]:
    """Every `vi.mock('.../api', ...)` call's full text."""
    spans = []
    for match in VI_MOCK_API.finditer(source):
        paren = source.index("(", match.start())
        spans.append(_call_span(source, paren))
    return spans


def _files_missing_the_export() -> set[str]:
    missing = set()
    for path in _test_files():
        source = path.read_text(encoding="utf-8", errors="replace")
        factories = _api_mock_factories(source)
        if not factories:
            continue
        if not any(REQUIRED_EXPORT in f for f in factories):
            missing.add(path.relative_to(FRONTEND).as_posix())
    return missing


# ---------------------------------------------------------------------------
# The premise: the export this is all about is real and is called unguarded
# ---------------------------------------------------------------------------

def test_use_entitlements_really_imports_and_calls_the_export() -> None:
    """If `useEntitlements` stopped importing it, this whole file is obsolete.

    Pinned so the ratchet cannot outlive its reason.
    """
    hook = FRONTEND / "src" / "hooks" / "useEntitlements.js"
    assert hook.is_file(), f"{hook} is missing"
    source = hook.read_text(encoding="utf-8")

    assert re.search(
        rf"import\s*\{{[^}}]*\b{REQUIRED_EXPORT}\b[^}}]*\}}\s*from\s*['\"][^'\"]*api['\"]",
        source,
    ), (
        f"useEntitlements no longer imports {REQUIRED_EXPORT} from the api module. If that is "
        "intentional, this file and its pinned list should be deleted, not updated."
    )
    assert f"{REQUIRED_EXPORT}()" in source, (
        f"{REQUIRED_EXPORT} is imported but never called, so omitting it from a mock would no "
        "longer throw and this ratchet guards nothing."
    )


# ---------------------------------------------------------------------------
# The ratchet, both directions
# ---------------------------------------------------------------------------

def test_no_new_api_mock_omits_isauthenticated() -> None:
    """Direction 1: a factory not on the pinned list must provide the export."""
    missing = _files_missing_the_export()
    unexpected = sorted(missing - KNOWN_INCOMPLETE_API_MOCKS)

    assert not unexpected, (
        f"these test files mock the api module without {REQUIRED_EXPORT!r}:\n\n"
        + "\n".join(f"  algo22-terminal/{f}" for f in unexpected)
        + f"\n\nA `vi.mock` factory replaces the whole module, so an omitted export throws on "
        f"access instead of falling through. `useEntitlements.load()` calls "
        f"{REQUIRED_EXPORT}() unguarded, inside an async function -- so this surfaces as an "
        "UNHANDLED ERROR with every test still reporting as passed, and vitest exits non-zero "
        "anyway. Three files have already done this.\n\n"
        "Add the export with a value that matches what the file means by signed in: read "
        "`sessionStorage.getItem('token')` if the file drives that store, `() => false` if it "
        "never signs in, or import the real helper if the point is that the app asks the HTTP "
        "client. See this module's docstring."
    )


def test_the_pinned_list_has_no_entries_that_are_already_fixed() -> None:
    """Direction 2: the list must shrink. An obsolete entry is a failure, not a comment.

    Without this, the list becomes an allowlist that grandfathers files nobody has looked at
    in a year, and the first rule stops meaning anything.
    """
    missing = _files_missing_the_export()
    already_fixed = sorted(KNOWN_INCOMPLETE_API_MOCKS - missing)

    assert not already_fixed, (
        "these files are on the pinned incomplete list but now declare "
        f"{REQUIRED_EXPORT!r}:\n\n"
        + "\n".join(f"  {f}" for f in already_fixed)
        + "\n\nDelete them from KNOWN_INCOMPLETE_API_MOCKS in this file. The list is a "
        "shrinking record of a known backlog, not a permanent exemption."
    )


def test_every_pinned_entry_still_exists_and_still_mocks_the_api_module() -> None:
    """A pinned path that has been renamed or no longer mocks the module is also stale."""
    stale = []
    for rel in sorted(KNOWN_INCOMPLETE_API_MOCKS):
        path = FRONTEND / rel
        if not path.is_file():
            stale.append(f"{rel} -- no such file (renamed or deleted?)")
            continue
        if not _api_mock_factories(path.read_text(encoding="utf-8", errors="replace")):
            stale.append(f"{rel} -- no longer mocks the api module at all")

    assert not stale, (
        "KNOWN_INCOMPLETE_API_MOCKS has stale entries:\n\n"
        + "\n".join(f"  {s}" for s in stale)
        + "\n\nRemove them. A list that names files which no longer exist cannot be trusted "
        "to describe the ones that do."
    )


# ---------------------------------------------------------------------------
# Controls: the detector must actually detect
# ---------------------------------------------------------------------------

class TestTheDetectorIsNotVacuous:
    """The three known instances, as fabricated source, plus what must not trip it."""

    def test_it_sees_a_factory_that_omits_the_export(self) -> None:
        source = "vi.mock('../../../src/api', () => ({ api: mockApi, default: mockApi }));"
        factories = _api_mock_factories(source)
        assert len(factories) == 1
        assert REQUIRED_EXPORT not in factories[0]

    def test_it_sees_a_factory_that_declares_the_export(self) -> None:
        source = (
            "vi.mock('../../api', () => ({ default: mockApi, "
            "isAuthenticated: () => !!sessionStorage.getItem('token') }));"
        )
        assert REQUIRED_EXPORT in _api_mock_factories(source)[0]

    def test_it_handles_an_async_factory_with_an_inner_import(self) -> None:
        """The shape `billingSocketLifecycle.test.jsx` uses -- braces, await, destructuring."""
        source = (
            "vi.mock('../../../src/api', async () => {\n"
            "  const { isAuthenticated, getToken } = await import('../../../src/apiClient');\n"
            "  const api = { billing: mockBilling };\n"
            "  return { api, endpoints: api, default: api, isAuthenticated, getToken };\n"
            "});"
        )
        factories = _api_mock_factories(source)
        assert len(factories) == 1, "the balanced scan must span the whole braced body"
        assert REQUIRED_EXPORT in factories[0]

    def test_it_does_not_match_the_apiclient_module(self) -> None:
        """`src/apiClient` is a different module and must not be swept in."""
        source = "vi.mock('../../src/apiClient', () => ({ get: mockGet }));"
        assert _api_mock_factories(source) == []

    def test_a_paren_inside_a_string_does_not_end_the_span(self) -> None:
        source = (
            "vi.mock('../../src/api', () => ({ "
            "default: { msg: 'a ) paren in a string' }, isAuthenticated: () => false }));"
        )
        factories = _api_mock_factories(source)
        assert len(factories) == 1
        assert REQUIRED_EXPORT in factories[0], (
            "the quote-aware scan must not stop at a paren inside a string literal"
        )

    def test_a_file_with_no_api_mock_is_not_reported(self) -> None:
        assert _api_mock_factories("import x from 'y'; describe('a', () => {});") == []
