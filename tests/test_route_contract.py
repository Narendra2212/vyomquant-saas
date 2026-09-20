"""
tests/test_route_contract.py — the frontend→backend route contract sweep.

`production-launch-hardening` task **9.1**. Requirements 1.25, 1.26, 1.27, 1.28 /
2.25, 2.26, 2.27, 2.28. Preservation 3.9.

═══════════════════════════════════════════════════════════════════════════════
WHY THIS FILE EXISTS, AND WHY IT COMES FIRST IN WAVE 4
═══════════════════════════════════════════════════════════════════════════════
Wave 4 fixes four broken frontend call paths. This file is the check that stops the
fourth from becoming a fifth: it enumerates every HTTP path the browser bundle
constructs and asserts each one appears in the backend's OpenAPI schema **with the
method the client uses**. 2.28 observes that the Copilot check generalises to
2.25-2.27; this is that generalisation, written once.

Landing the sweep before the four fixes is deliberate. On the tree it was written
against it **fails**, and the failure list is the measurement of cluster D — five
findings over four modules, named below. Landing it afterwards would have made it a
test that has never failed, which is the one thing a contract guard cannot afford to
be.

═══════════════════════════════════════════════════════════════════════════════
WHAT IT IS NOT A DUPLICATE OF
═══════════════════════════════════════════════════════════════════════════════
`algo22-terminal/tests/unit/guards/api-paths.test.js` reads the same two halves and
is not replaced. Two differences make this file necessary rather than redundant:

  * **It reads the live route table, not the decorators.** `api-surface.js` derives
    the backend surface by parsing `main.py`'s `include_router(...)` calls and each
    router's decorators. That is a reconstruction. This file imports the app and asks
    it — `app.openapi()["paths"]` — so route ordering, `include_in_schema`, mount
    prefixes carried on the `APIRouter(...)` constructor and aliased router imports
    are all resolved by FastAPI itself rather than re-derived.
  * **It does not require a call to mention `/api`.** The JS guard's scan is anchored
    on `/api`, and all three engine-context defects name paths that do not contain
    it — `/indicator/compute`, `/logic/evaluate`, `/strategy/execute`. They were
    therefore invisible to it. That is the hole 1.25-1.27 sat in.

═══════════════════════════════════════════════════════════════════════════════
PRESERVATION 3.9
═══════════════════════════════════════════════════════════════════════════════
Nothing here sends a request, authenticates, or constructs a `TestClient`. It reads
the route table and the frontend sources. No route's authentication dependency, rate
limit or ownership predicate is touched by adding it.

Verification:
    python -m pytest tests/test_route_contract.py -v -p no:cacheprovider
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

FRONTEND_SRC = REPO_ROOT / "algo22-terminal" / "src"


# ═════════════════════════════════════════════════════════════════════════════
# 1. THE CLIENT SIDE — every path the bundle constructs
# ═════════════════════════════════════════════════════════════════════════════

# The transport helpers `src/apiClient.js` exports, and the verb each one issues.
# `publicGet` is the unauthenticated GET; `del` is the exported name for DELETE
# because `delete` is a reserved word.
CLIENT_VERBS = {
    "get": "GET",
    "publicGet": "GET",
    "post": "POST",
    "put": "PUT",
    "patch": "PATCH",
    "del": "DELETE",
}

# Raw transports. `fetch` bypasses the client entirely; `authedFetch` is
# `pages/StrategyDetail.jsx`'s own wrapper over it. Both carry their verb in an
# options object rather than in the function name.
RAW_TRANSPORTS = ("fetch", "authedFetch")

ALL_TRANSPORTS = tuple(CLIENT_VERBS) + RAW_TRANSPORTS

# A call to one of the above, as a bare identifier. The negative lookbehind keeps
# `map.get(`, `sessionStorage.getItem(` and `queryClient.fetchQuery(` out: a method on
# an object is not this client.
CALL_RE = re.compile(
    r"(?<![\w.$])(" + "|".join(re.escape(name) for name in ALL_TRANSPORTS) + r")\s*\("
)

# `const NAME = '/api/...'`, and the destructuring-default form
# `({ children, apiBaseUrl = "/api/v1/copilot" })`. Both are how a mount prefix gets a
# name in this tree, and a path built from one has to be resolved through it or the
# call reads as addressing nothing.
BINDING_RE = re.compile(r"""([A-Za-z_$][\w$]*)\s*=\s*["'`](/[^"'`\n${]*)["'`]""")

METHOD_OPTION_RE = re.compile(r"""\bmethod\s*:\s*["'`]([A-Za-z]+)["'`]""")

# `import { get, post } from '../apiClient'` / `from '../api'`. Which names a file takes
# from the client decides how a bare `get(` in it is read — see `scan_frontend`.
IMPORT_RE = re.compile(r"""import\s*\{([^}]*)\}\s*from\s*["']([^"']+)["']""")
CLIENT_MODULE_RE = re.compile(r"(^|/)(apiClient|api)(\.js)?$")

# `src/apiClient.js` is the transport itself, not a call site. Its one `fetch(fullUrl, …)`
# is `publicGet`'s implementation and `fullUrl` is assembled from the caller's own `url`,
# so every path that passes through it is already read at the call site. Its remaining
# requests go through `client.get(…)` on the axios instance, which the lookbehind in
# `CALL_RE` excludes for the same reason it excludes `map.get(`.
SKIP_FILES = {"algo22-terminal/src/apiClient.js"}


def strip_comments(source: str) -> str:
    """
    `source` with every comment replaced by spaces, string and template literals
    preserved byte for byte.

    Not cosmetic. These modules document their own route tables in docblocks — e.g.
    ``* `@router.post("/cancel-all")` `` in `src/api/modules/orders.js` — so a scan
    that read comments would invent call sites that do not exist. Offsets are
    preserved so a reported line number is the real one.
    """
    out = []
    i = 0
    n = len(source)
    while i < n:
        ch = source[i]
        if ch in "\"'`":
            end = _literal_end(source, i)
            out.append(source[i:end])
            i = end
            continue
        if ch == "/" and i + 1 < n:
            nxt = source[i + 1]
            if nxt == "/":
                end = source.find("\n", i)
                end = n if end == -1 else end
                out.append(" " * (end - i))
                i = end
                continue
            if nxt == "*":
                end = source.find("*/", i + 2)
                end = n if end == -1 else end + 2
                chunk = source[i:end]
                # Keep the newlines so line numbers survive.
                out.append("".join("\n" if c == "\n" else " " for c in chunk))
                i = end
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _literal_end(source: str, start: int) -> int:
    """
    The index just past the literal opening at `start`.

    Backticks recurse through `${...}` so a nested template — the ternary query-suffix
    form — does not terminate the outer literal at its own first backtick. A `'`/`"`
    literal that does not close on its line ends at the newline, so unbalanced quotes
    in JSX prose cannot swallow the rest of the file.
    """
    quote = source[start]
    i = start + 1
    n = len(source)
    while i < n:
        c = source[i]
        if c == "\\":
            i += 2
            continue
        if c == quote:
            return i + 1
        if quote == "`" and c == "$" and i + 1 < n and source[i + 1] == "{":
            depth = 1
            i += 2
            while i < n and depth:
                d = source[i]
                if d == "\\":
                    i += 2
                    continue
                if d in "\"'`":
                    i = _literal_end(source, i)
                    continue
                if d == "{":
                    depth += 1
                elif d == "}":
                    depth -= 1
                i += 1
            continue
        if quote != "`" and c == "\n":
            return i
        i += 1
    return n


def _args_end(source: str, open_paren: int) -> int:
    """The index just past the `)` closing the call whose `(` is at `open_paren`."""
    depth = 0
    i = open_paren
    n = len(source)
    while i < n:
        c = source[i]
        if c in "\"'`":
            i = _literal_end(source, i)
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def _first_arg(source: str, open_paren: int, close_paren: int) -> str:
    """The call's first argument, as source text, split at the first top-level comma."""
    i = open_paren + 1
    depth = 0
    while i < close_paren - 1:
        c = source[i]
        if c in "\"'`":
            i = _literal_end(source, i)
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            return source[open_paren + 1 : i]
        i += 1
    return source[open_paren + 1 : close_paren - 1]


def _split_literals(text: str) -> tuple[list[str], list[str]]:
    """
    `(literals, gaps)` for `text` — every string or template literal, quotes included,
    and the source between consecutive literals.

    The gaps are what tell the two multi-literal forms apart, which is why they are
    returned rather than discarded. See `literal_groups`.
    """
    literals: list[str] = []
    gaps: list[str] = []
    i = 0
    cursor = 0
    n = len(text)
    while i < n:
        if text[i] in "\"'`":
            end = _literal_end(text, i)
            gaps.append(text[cursor:i])
            literals.append(text[i:end])
            cursor = end
            i = end
            continue
        i += 1
    gaps.append(text[cursor:])
    return literals, gaps


def literal_groups(first_arg: str) -> list[str]:
    """
    The first argument of a transport call, as one literal body per address it can
    address.

    Two multi-literal forms occur in this tree and they mean opposite things:

      * **`+`-folded**, because the line got long. `src/api/modules/strategies.js:325`
        writes ``post(`/api/strategies/${id}` + `/versions/${v}/deploy`, …)``. That is
        **one** address in two pieces, and reading the pieces separately reports
        `/versions/{}/deploy` — a path no router declares, from code that is correct.
      * **ternary-branched**, because the query string is conditional.
        `src/api/modules/billing.js:61` writes
        ``get(currency ? `/api/billing/plans?currency=${…}` : '/api/billing/plans')``.
        That is **two** addresses and both are real.

    The gaps between the literals decide: gaps that are only whitespace or `+` fold into
    one body; anything else (a `?`, a `:`, a comma) keeps them separate.
    """
    literals, gaps = _split_literals(first_arg)
    if not literals:
        return []
    inner_gaps = [gap.strip() for gap in gaps[1:-1]] if len(literals) > 1 else []
    if inner_gaps and all(gap in {"", "+"} for gap in inner_gaps):
        return ["".join(literal[1:-1] for literal in literals)]
    return [literal[1:-1] for literal in literals]


def _interpolations(body: str):
    """Yield `(start, end, inner)` for each top-level `${...}` in a template body."""
    i = 0
    n = len(body)
    while i < n:
        if body[i] == "$" and i + 1 < n and body[i + 1] == "{":
            depth = 1
            j = i + 2
            while j < n and depth:
                if body[j] in "\"'`":
                    j = _literal_end(body, j)
                    continue
                if body[j] == "{":
                    depth += 1
                elif body[j] == "}":
                    depth -= 1
                j += 1
            yield i, j, body[i + 2 : j - 1]
            i = j
            continue
        i += 1


def resolve_body(body: str, bindings: dict[str, str]) -> str | None:
    """
    The request path a literal body addresses, or `None` when it addresses no local
    route — an absolute URL, a relative asset path, or a wrapper's `${base}${path}`
    forwarding form, which carries no literal text of its own.

    Three substitutions, each for a form that occurs in this tree:

      * a **leading** `${NAME}` resolves through `bindings` when the file gives that
        name a path value (`CopilotContext`'s `apiBaseUrl = "/api/v1/copilot"`), and
        otherwise contributes nothing — `${API_BASE}` is the origin, and
        `CONFIG.apiBaseUrl` carries no path (`src/config.js` returns
        `window.location.origin`);
      * an interpolation whose text contains a `?` is a **query suffix**, not a path
        segment, and contributes nothing. This is the
        ``/api/orders/history${limit ? `?limit=${limit}` : ''}`` form, and blanking it
        to a placeholder instead would report a correct call as addressing
        `/api/orders/history{}`;
      * every other interpolation is a **path parameter** and becomes `{}`, which is
        what the backend's own `{order_id}` normalises to.
    """
    pieces = []
    cursor = 0
    for start, end, inner in _interpolations(body):
        pieces.append(body[cursor:start])
        text = inner.strip()
        if start == 0 and text in bindings:
            pieces.append(bindings[text])
        elif start == 0:
            pieces.append("")
        elif "?" in inner:
            pieces.append("")
        else:
            pieces.append("{}")
        cursor = end
    pieces.append(body[cursor:])
    path = "".join(pieces)

    if not path.startswith("/"):
        return None
    path = path.split("?", 1)[0].split("#", 1)[0]
    path = re.sub(r"/{2,}", "/", path)
    if len(path) > 1:
        path = path.rstrip("/")
    return path


@dataclass(frozen=True)
class CallSite:
    method: str
    path: str
    where: str
    line: int

    def __str__(self) -> str:
        return f"{self.method} {self.path}  ({self.where}:{self.line})"


def frontend_files() -> list[Path]:
    found = []
    for dirpath, dirnames, filenames in os.walk(FRONTEND_SRC):
        dirnames[:] = [d for d in dirnames if d not in {"node_modules", "__tests__"}]
        for name in filenames:
            if name.endswith((".js", ".jsx")) and ".test." not in name:
                found.append(Path(dirpath) / name)
    return sorted(found)


@lru_cache(maxsize=1)
def scan_frontend() -> tuple[tuple[CallSite, ...], frozenset[str]]:
    """
    Every resolvable transport call in the bundle, and every site whose address could
    not be read there.

    The second set is returned rather than discarded: a call site the scanner cannot
    read is a hole in the check, and a hole that reports as nothing is how this class of
    defect reached production four times. Keyed `<file>|<transport>` with no line
    number, so moving code does not churn the recorded list and two occurrences of one
    wrapper in one file are one entry.

    Cached: the walk reads about 250 sources and every assertion below needs the same
    answer.
    """
    calls: list[CallSite] = []
    unresolvable: set[str] = set()

    for file in frontend_files():
        rel = file.relative_to(REPO_ROOT).as_posix()
        if rel in SKIP_FILES:
            continue

        raw = file.read_text(encoding="utf-8", errors="replace")
        code = strip_comments(raw)
        bindings = {name: value for name, value in BINDING_RE.findall(code)}

        # The names this file takes from the client. `get` imported from `../apiClient`
        # is the transport; `get` destructured from a Zustand creator
        # (`src/store/useExecutionStore.js`) is the store reader and takes no path at
        # all. The distinction matters in both directions and is made below.
        imported = set()
        for names, module in IMPORT_RE.findall(code):
            if CLIENT_MODULE_RE.search(module):
                imported.update(
                    part.strip().split(" as ")[-1].strip()
                    for part in names.split(",")
                    if part.strip()
                )

        for match in CALL_RE.finditer(code):
            name = match.group(1)
            open_paren = match.end() - 1
            close_paren = _args_end(code, open_paren)
            line = code.count("\n", 0, match.start()) + 1

            if name in CLIENT_VERBS:
                method = CLIENT_VERBS[name]
            else:
                option = METHOD_OPTION_RE.search(code[open_paren:close_paren])
                method = option.group(1).upper() if option else "GET"

            first_arg = _first_arg(code, open_paren, close_paren)
            paths = [
                path
                for path in (
                    resolve_body(body, bindings) for body in literal_groups(first_arg)
                )
                if path is not None
            ]

            if paths:
                for path in paths:
                    calls.append(CallSite(method, path, rel, line))
                continue

            # No path in the first argument.
            #
            # For a name this file imported from the client, that is a hole in the
            # check — the call is definitely a request and its address is definitely
            # unread — so it is reported rather than dropped. `fetch` and `authedFetch`
            # are always transports and are treated the same way.
            #
            # For a bare `get(`/`post(` the file never imported, it is not a request at
            # all: Zustand's `get()` and `set()` readers are the case, and they carry no
            # argument. Counting those would be noise that buries a real finding.
            if name in imported or name in RAW_TRANSPORTS:
                unresolvable.add(f"{rel}|{name}")

    return tuple(calls), frozenset(unresolvable)


# ═════════════════════════════════════════════════════════════════════════════
# 2. THE BACKEND SIDE — the OpenAPI schema, asked of the app itself
# ═════════════════════════════════════════════════════════════════════════════

PARAM_RE = re.compile(r"\{[^{}]*\}")


def normalise_route(path: str) -> str:
    """`/api/strategies/{strategy_id}` → `/api/strategies/{}`."""
    path = PARAM_RE.sub("{}", path)
    return path.rstrip("/") if len(path) > 1 else path


@pytest.fixture(scope="module")
def schema_methods() -> dict[str, set[str]]:
    """Normalised path → the set of verbs the OpenAPI schema declares for it."""
    from backend_app.main import app

    table: dict[str, set[str]] = {}
    for path, operations in app.openapi()["paths"].items():
        key = normalise_route(path)
        verbs = {
            verb.upper()
            for verb in operations
            if verb.lower()
            in {"get", "post", "put", "patch", "delete", "head", "options"}
        }
        table.setdefault(key, set()).update(verbs)
    return table


@pytest.fixture(scope="module")
def frontend_calls() -> tuple[CallSite, ...]:
    calls, _ = scan_frontend()
    return calls


@pytest.fixture(scope="module")
def unread_sites() -> frozenset[str]:
    _, unresolvable = scan_frontend()
    return unresolvable


def candidate_paths(path: str) -> list[str]:
    """
    The addresses a client path may be, most specific first.

    Normally one. The exception is a placeholder **glued to the previous segment**
    rather than separated from it by a `/`:

        /api/paper/sessions{}              ← `get(`/api/paper/sessions${q(params)}`)`
        /api/strategies/{}                 ← `get(`/api/strategies/${id}`)`

    The first is `src/api/modules/paper.js:135`, where the interpolation is a query
    builder — `sessionListQuery(params)` returns `?session_state=…` — so the address is
    `/api/paper/sessions` and the placeholder is not part of the path at all. The second
    is a path parameter. Nothing in the source text distinguishes them without
    evaluating the function, so a glued placeholder yields both readings and the call
    resolves if either is declared.

    A `/`-separated placeholder yields only itself, which keeps the ordinary
    `/collection/${id}` form held to the parameterised route and not quietly satisfied
    by the collection route above it.
    """
    candidates = [path]
    if path.endswith("{}") and len(path) > 2 and path[-3] != "/":
        trimmed = path[:-2].rstrip("/")
        candidates.append(trimmed or "/")
    return candidates


def _verbs_for(path: str, schema: dict[str, set[str]]) -> set[str] | None:
    """
    The verbs declared for `path`, matched segment-wise so a client path carrying a
    concrete id where the route declares a parameter still matches: `{}` on the route
    side matches any single segment.
    """
    if path in schema:
        return schema[path]
    segments = path.strip("/").split("/")
    for route, route_verbs in schema.items():
        route_segments = route.strip("/").split("/")
        if len(route_segments) != len(segments):
            continue
        if all(
            route_segment in ("{}", segment)
            for route_segment, segment in zip(route_segments, segments)
        ):
            return route_verbs
    return None


def resolves(call: CallSite, schema: dict[str, set[str]]) -> str | None:
    """`None` when the call resolves; otherwise why it does not."""
    declared: set[str] = set()
    matched = False
    for candidate in candidate_paths(call.path):
        verbs = _verbs_for(candidate, schema)
        if verbs is None:
            continue
        matched = True
        if call.method in verbs:
            return None
        declared |= verbs

    if not matched:
        return "no route declares this path under any verb"
    return f"declared for {'/'.join(sorted(declared))}, not {call.method}"


# ═════════════════════════════════════════════════════════════════════════════
# 3. THE RECORDED SET — what does not resolve, and who owns it
# ═════════════════════════════════════════════════════════════════════════════
#
# Same ratchet as `algo22-terminal/tests/unit/guards/api-paths.budget.js`: the measured
# set must equal these keys **exactly**.
#
#   * an unrecorded mismatch  → a call was added that the backend does not serve.
#   * a recorded one that is gone → it was fixed; delete the line in the same commit.
#
# A `<=` assertion would let one contributor fix ten of these and leave room for the
# next to add ten back without CI noticing.
#
# Key format: `<METHOD> <path>` — the address, not the call site, so moving code does
# not churn the list and two callers of one dead address share one entry.

KNOWN_UNRESOLVED = {
    # ── Not wave 4's. Recorded here because this is the first check that can see
    # them: both are `contexts/DataPipelineContext.jsx` reads issued without the
    # `/api/market` mount prefix, so they address the origin root. `market.py`
    # declares `GET /data/historical` and `GET /data/orderbook` **relative to that
    # router**, and `main.py:639` mounts it at `/api/market` — so the routes that
    # exist are `/api/market/data/historical` and `/api/market/data/orderbook`, and
    # the two calls below reach neither. They are the same defect class as 1.25-1.27
    # and are left for the task that owns the data pipeline: fixing them here would
    # mix a detection change with a behaviour change in the builder's data path.
    "GET /data/historical": (
        "contexts/DataPipelineContext.jsx fetchOHLCV; the route is "
        "GET /api/market/data/historical."
    ),
    "GET /data/orderbook": (
        "contexts/DataPipelineContext.jsx fetchOrderbookImbalance; the route is "
        "GET /api/market/data/orderbook."
    ),
}


# Transport call sites whose address is not readable where the call is written, keyed
# `<file>|<transport>`. Same exact-match ratchet as `KNOWN_UNRESOLVED`.
#
# Both entries below are one construct: `pages/StrategyDetail.jsx` defines a local
# `authedFetch = (path, options) => fetch(`${API_BASE}${path}`, …)` wrapper — twice, once
# per component in the file. The address is the wrapper's `path` argument, and every
# caller of it *is* read, as an `authedFetch('/api/strategies/…')` site. So the coverage
# is real; what is unreadable is the forwarding line, not the request.
UNREAD_TRANSPORT_SITES = {
    "algo22-terminal/src/pages/StrategyDetail.jsx|fetch": (
        "the page's own `authedFetch` wrapper, `fetch(`${API_BASE}${path}`, …)`. Its "
        "callers are read as authedFetch(...) sites and carry the path."
    ),
}

# Floors on how much the sweep must still be reading. Floors rather than equalities:
# adding a route or a call must not fail CI, but the scanner quietly reading *nothing*
# and reporting green must. A guard that examines zero paths passes for free.
# Measured when this landed: 302 schema paths, 206 resolved call sites. Both floors are
# set about 10% below, so ordinary churn does not touch them and a collapse does.
ROUTES_READ_AT_LEAST = 270
CALLS_CHECKED_AT_LEAST = 180


# ═════════════════════════════════════════════════════════════════════════════
# 4. THE ASSERTIONS
# ═════════════════════════════════════════════════════════════════════════════


class TestTheSweepIsActuallyReading:
    """A contract guard that reads nothing passes for free. These four say it does."""

    def test_the_frontend_tree_was_found(self):
        files = frontend_files()
        assert FRONTEND_SRC.is_dir(), f"{FRONTEND_SRC} is not a directory"
        assert len(files) > 100, (
            f"only {len(files)} frontend sources found under {FRONTEND_SRC}; the walk "
            "is not reading the bundle"
        )

    def test_the_route_table_was_read(self, schema_methods):
        assert len(schema_methods) >= ROUTES_READ_AT_LEAST, (
            f"the OpenAPI schema resolved {len(schema_methods)} paths, below the floor "
            f"of {ROUTES_READ_AT_LEAST}. Either routers stopped being mounted or the "
            "schema is no longer being read."
        )

    def test_enough_call_sites_were_checked(self, frontend_calls):
        assert len(frontend_calls) >= CALLS_CHECKED_AT_LEAST, (
            f"only {len(frontend_calls)} transport call sites resolved to a path, below "
            f"the floor of {CALLS_CHECKED_AT_LEAST}. The scanner has stopped reading "
            "the client."
        )

    def test_comments_are_not_read_as_call_sites(self):
        """
        The scan must not invent a call site from a docblock. `src/api/modules/orders.js`
        quotes `@router.post("/cancel-all")` in prose, and several modules quote the
        decorator their call is aimed at.
        """
        source = '''
        // post('/ghost/one', {})
        /** post('/ghost/two', {}) */
        const real = () => post('/api/health', {});
        '''
        code = strip_comments(source)
        assert "/ghost/one" not in code
        assert "/ghost/two" not in code
        assert "/api/health" in code


class TestEveryFrontendCallPathResolves:
    """
    Requirements 2.25-2.28. The clause is a disjunction — a call reaches an implemented
    endpoint, **or** the client-side path does not exist — so a removed call path
    satisfies it by not being scanned at all.
    """

    def test_no_unrecorded_mismatch(self, frontend_calls, schema_methods):
        findings: dict[str, list[str]] = {}
        for call in frontend_calls:
            why = resolves(call, schema_methods)
            if why is None:
                continue
            findings.setdefault(f"{call.method} {call.path}", []).append(
                f"{call.where}:{call.line} — {why}"
            )

        measured = set(findings)
        recorded = set(KNOWN_UNRESOLVED)

        unrecorded = sorted(measured - recorded)
        assert not unrecorded, (
            "Frontend call paths that no backend route serves as written:\n  "
            + "\n  ".join(
                f"{key}\n      " + "\n      ".join(findings[key]) for key in unrecorded
            )
            + "\n\nEither point the call at a route that exists, remove the call path, "
            "or record it in KNOWN_UNRESOLVED with the task that owns it."
        )

    def test_no_recorded_mismatch_has_been_fixed_without_clearing_its_line(
        self, frontend_calls, schema_methods
    ):
        measured = {
            f"{call.method} {call.path}"
            for call in frontend_calls
            if resolves(call, schema_methods) is not None
        }
        stale = sorted(set(KNOWN_UNRESOLVED) - measured)
        assert not stale, (
            "These are recorded in KNOWN_UNRESOLVED but now resolve, or are no longer "
            f"called at all: {stale}. Delete the lines in the same commit as the fix — "
            "a list that only ever grows stops being a ratchet."
        )

    def test_no_unrecorded_unreadable_call_site(self, unread_sites):
        """
        A transport call whose address cannot be read where it is written is a hole in
        this check, not a pass. Reported by name so it cannot be silently dropped.
        """
        unrecorded = sorted(unread_sites - set(UNREAD_TRANSPORT_SITES))
        assert not unrecorded, (
            "Transport calls whose path this sweep could not read:\n  "
            + "\n  ".join(unrecorded)
            + "\n\nGive the call a literal path, teach the scanner the form, or record "
            "it in UNREAD_TRANSPORT_SITES with why the address is readable elsewhere."
        )

    def test_no_recorded_unreadable_site_has_gone_without_its_line(self, unread_sites):
        stale = sorted(set(UNREAD_TRANSPORT_SITES) - unread_sites)
        assert not stale, (
            f"recorded in UNREAD_TRANSPORT_SITES but no longer measured: {stale}. "
            "Delete the lines in the same commit."
        )


class TestTheFourInstancesWave4Owns:
    """
    The five findings this wave exists to clear, asserted one address at a time so the
    failure names the capability rather than a count.

    Tasks 9.2 and 9.3 turn these green — 9.2 by removing three dead call paths, 9.3 by
    deleting the dormant Copilot module. Both satisfy the requirement's second
    disjunct: the path is gone, so there is nothing left to resolve.
    """

    WAVE_4_ADDRESSES = [
        ("POST", "/indicator/compute", "IndicatorEngineContext.jsx", "1.25"),
        ("POST", "/logic/evaluate", "LogicEngineContext.jsx", "1.26"),
        ("POST", "/strategy/execute", "StrategyEngineContext.jsx", "1.27"),
        ("POST", "/api/v1/copilot/dag/generate", "CopilotContext.jsx", "1.28"),
        ("GET", "/api/v1/copilot/sessions/{}", "CopilotContext.jsx", "1.28"),
    ]

    @pytest.mark.parametrize(
        "method,path,owner,requirement",
        WAVE_4_ADDRESSES,
        ids=[f"{r} {m} {p}" for m, p, _f, r in WAVE_4_ADDRESSES],
    )
    def test_the_address_is_gone_or_resolves(
        self, frontend_calls, schema_methods, method, path, owner, requirement
    ):
        sites = [
            call
            for call in frontend_calls
            if call.method == method and call.path == path
        ]
        if not sites:
            return  # the call path was removed — the requirement's second disjunct

        failures = [
            f"{call.where}:{call.line} — {resolves(call, schema_methods)}"
            for call in sites
            if resolves(call, schema_methods) is not None
        ]
        assert not failures, (
            f"Requirement {requirement} ({owner}): {method} {path} is still called and "
            "still does not resolve:\n  " + "\n  ".join(failures)
        )
