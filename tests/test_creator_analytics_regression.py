"""tests/test_creator_analytics_regression.py — the revert-detector for Task 21.1.

Feature: marketplace-subscriptions-paper-trading, task 21.2.
Design reference: ``design.md`` -> "Root-cause fixes" -> "3. ``creator_analytics``".
Requirements 1.4, 1.5, 1.7, 10.6, 10.7.

WHAT THIS FILE IS FOR
---------------------
``creator_analytics`` shipped three compounding defects in one handler:

1. ``.select("id, name, clone_count, monthly_price, rating_average")`` — ``monthly_price`` and
   ``rating_average`` do **not** exist on ``library_strategies``, so the query fails with
   PostgreSQL ``42703``. That is the identical condition the header of
   ``migrations/007_add_marketplace_pricing_columns.sql`` records for ``/trending`` and
   ``/featured``.
2. A bare ``except Exception: return {…every figure 0.0…}`` swallowed that failure and answered
   **HTTP 200 with a fabricated zero** — the substitution Requirements 1.5, 1.7 and 28.2 forbid.
   A creator was shown ``$0.00`` earnings for a query that never ran, indistinguishable from a
   creator who genuinely earned nothing.
3. ``mrr = sum(clone_count * float(monthly_price))`` then ``round(mrr * 0.90, 2)`` — an earnings
   figure derived from a *counter multiplied by a current price* (Requirement 10.6 forbids
   exactly that), in binary floating point (Requirement 10.3 forbids exactly that).

Every assertion below therefore states the **post-21.1** behaviour and consequently FAILS
against the pre-21.1 handler. That failure is the finding, and it is what makes this file a
revert-detector rather than a description: reinstate any of the three defects and this file goes
red.

THE FOUR CLAIMS
---------------
``TestReachable``          the endpoint resolves to ``creator_analytics`` and does not answer 422
                          from ``_safe_uuid("analytics", "creator_id")`` (task 13.1's fix, whose
                          survival every other claim here depends on)
``TestFailedReadRefuses``  a read that did not complete answers 500/503, carries the stable
                          ``MARKETPLACE_READ_FAILED`` code, and carries **no** numeric payload
                          (Requirements 1.5, 1.7)
``TestEarningsAreLedgerSums``  the reported owner total is the exact integer sum of
                          ``owner_share_minor`` over non-reversal rows minus reversal rows, PER
                          CURRENCY, and equals no product of a counter and a price
                          (Requirements 10.6, 10.7)
``TestNoFabricatedFigure`` the deleted constructs stay deleted: the two non-existent columns, the
                          float arithmetic, the counter×price product, the bare ``except`` with a
                          zero-filled body, and the invented
                          ``payout_schedule: "Monthly auto-transfer (Stripe Connect)"``

WHY THE 42703 IS SIMULATED BY A COLUMN-AWARE DOUBLE AND NOT BY A RAISING STUB
----------------------------------------------------------------------------
:class:`_FakeSupabase` is told which columns each table actually has, and raises
:class:`_UndefinedColumn` (SQLSTATE ``42703``, shaped like a ``postgrest`` ``APIError``) for a
``select`` that names one that does not exist. So the pre-fix failure this file observes is the
**real** defect — a handler asking for ``monthly_price`` — rather than a failure staged by the
test. A stub that raised unconditionally would pass just as well against a handler that selected
the right columns, and would therefore not detect a revert to the wrong ones.

WHY THE LEDGER ORACLE IS NOT ``creator_earnings``
-------------------------------------------------
:func:`_owner_total_oracle` accumulates Requirement 10.7's formula over the rows the double was
*seeded* with, in a plain Python loop, and never calls the production earnings read. It is the
same quantity ``tests/property/test_settlement_ledger.py::test_p5_reported_totals_equal_ledger_sums``
(task 19.5) states as a property over generated settlement sequences — deliberately, so the two
cannot disagree about what an owner is owed. That property owns the *generated-sequence* claim
and the independent 90/10 split oracle; this file owns the claim that **the HTTP endpoint reports
that same quantity**, which is a different statement and is not duplicated there.

WHY ``_run_coroutine`` IS HERE AT ALL
-------------------------------------
Every HTTP claim goes through ``TestClient``, which runs its own loop. ``_run_coroutine`` is used
for the one assertion that calls the handler directly (to observe the raised
``MarketplaceError`` rather than its rendered body), and it is the helper from
``tests/test_checkout_service.py::_run`` rather than ``asyncio.run``: ``asyncio.run`` closes its
loop and then leaves the thread with *no* current loop, which breaks the neighbouring suites that
still call ``asyncio.get_event_loop()``.

WHAT IS DELIBERATELY NOT ASSERTED HERE
--------------------------------------
* The 90/10 split itself, and the per-currency totals over generated sequences — P-1 … P-5,
  ``tests/property/test_money_split.py`` and ``tests/property/test_settlement_ledger.py``.
* The error envelope's shape and scrubbing across every endpoint and every failure mode —
  ``tests/test_marketplace_error_surface.py`` (task 16.5), which enumerates the whole read
  surface from ``app.router.routes``.
* Route registration order in general — ``tests/test_library_route_resolution.py`` (task 13.1).
  Only the one path this file drives is re-checked, because a 422 here would otherwise be
  misread as a defect in the handler.
* Anything about ``marketplace_listings`` or ``strategy_subscriptions``: both stay dormant
  (Requirement 1.2) and neither is referenced.
"""

from __future__ import annotations

import ast
import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Sequence, Set, Tuple

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient
from starlette.routing import Match

from backend_app.backend.marketplace import errors
from backend_app.core.dependencies import get_current_user
from backend_app.main import app
from backend_app.routers import library as library_router

# ══════════════════════════════════════════════════════════════════════════
# Fixed identities and the path under test
# ══════════════════════════════════════════════════════════════════════════

ANALYTICS_PATH = "/api/library/creator/analytics"

#: The authenticated creator. The identity travels as a PREDICATE on the read
#: (``.eq("author_id", …)`` and ``.eq("owner_id", …)``), so another creator's earnings are
#: unreachable by guessing an id — which
#: :meth:`TestEarningsAreLedgerSums.test_another_creators_earnings_are_unreachable` asserts.
OWNER_ID = "11111111-1111-1111-1111-111111111111"
OTHER_OWNER_ID = "22222222-2222-2222-2222-222222222222"

OWNER: Dict[str, Any] = {
    "id": OWNER_ID,
    "email": "creator@test.vyomquant.io",
    "role": "authenticated",
    "app_metadata": {"role": "authenticated"},
    "user_metadata": {},
}

REPO_ROOT: Path = Path(__file__).resolve().parents[1]
LIBRARY_ROUTER_SOURCE: Path = REPO_ROOT / "backend_app" / "routers" / "library.py"

client = TestClient(app, raise_server_exceptions=False)


# ══════════════════════════════════════════════════════════════════════════
# The Persistence_Layer double: column-aware, so a 42703 is EARNED
# ══════════════════════════════════════════════════════════════════════════


class _UndefinedColumn(Exception):
    """PostgreSQL ``42703``, shaped like a ``postgrest`` ``APIError``.

    Carries a ``code`` attribute and an ``args[0]`` mapping, which is what a handler inspecting
    either would see from the real driver. The message names the column, so a body that echoed it
    would be caught by ``tests/test_marketplace_error_surface.py``'s leak assertions.
    """

    def __init__(self, table: str, column: str) -> None:
        self.code = "42703"
        self.message = f"column {table}.{column} does not exist"
        super().__init__({"code": self.code, "message": self.message})


#: The columns ``library_strategies`` actually has, restricted to the ones an analytics read could
#: plausibly want. Sourced from ``archived_migrations/root_migrations/001_create_library_strategies.sql``
#: and ``backend_app/migrations/007_add_marketplace_pricing_columns.sql``. ``monthly_price`` and
#: ``rating_average`` are ABSENT on purpose: they are the two columns Requirement 1.4 forbids
#: referencing, and their absence here is what turns the pre-fix ``.select`` into a real 42703.
LIBRARY_STRATEGIES_COLUMNS: FrozenSet[str] = frozenset(
    {
        "id",
        "name",
        "author_id",
        "moderation_status",
        "clone_count",
        "subscriber_count",
        "price",
        "price_minor",
        "currency",
        "avg_rating",
        "rating_count",
        "published_at",
        "is_active",
    }
)

#: The Settlement_Ledger's columns (``backend_app/migrations/008_marketplace_settlement.sql``
#: section 2, column for column).
MARKETPLACE_SETTLEMENTS_COLUMNS: FrozenSet[str] = frozenset(
    {
        "id",
        "subscription_id",
        "listing_id",
        "owner_id",
        "purchaser_id",
        "amount_minor",
        "owner_share_minor",
        "platform_fee_minor",
        "currency",
        "provider",
        "provider_reference",
        "is_reversal",
        "reverses_reference",
        "settled_at",
    }
)

TABLE_COLUMNS: Mapping[str, FrozenSet[str]] = {
    "library_strategies": LIBRARY_STRATEGIES_COLUMNS,
    "marketplace_settlements": MARKETPLACE_SETTLEMENTS_COLUMNS,
}


class _Response:
    """What ``supabase-py`` hands back: rows on ``.data``, no ``error``."""

    def __init__(self, data: Any) -> None:
        self.data = data
        self.error = None
        self.count = len(data) if isinstance(data, list) else None


class _Query:
    """One table query. Records the projection and the predicates, filters on ``.execute()``."""

    def __init__(self, fake: "_FakeSupabase", table: str) -> None:
        self._fake = fake
        self._table = table
        self._columns: Optional[List[str]] = None
        self._filters: List[Tuple[str, Any]] = []
        self._single = False

    # ── the projection ────────────────────────────────────────────────────
    def select(self, columns: str = "*", *_a: Any, **_k: Any) -> "_Query":
        self._columns = [part.strip() for part in str(columns).split(",") if part.strip()]
        self._fake.selects.append((self._table, tuple(self._columns)))
        return self

    # ── the predicates ────────────────────────────────────────────────────
    def eq(self, column: str, value: Any) -> "_Query":
        self._filters.append((column, value))
        return self

    def in_(self, column: str, values: Sequence[Any]) -> "_Query":
        self._filters.append((column, list(values)))
        return self

    def order(self, *_a: Any, **_k: Any) -> "_Query":
        return self

    def limit(self, *_a: Any, **_k: Any) -> "_Query":
        return self

    def range(self, *_a: Any, **_k: Any) -> "_Query":
        return self

    def single(self) -> "_Query":
        self._single = True
        return self

    # ── the read ──────────────────────────────────────────────────────────
    def execute(self) -> _Response:
        known = TABLE_COLUMNS[self._table]
        for column in self._columns or []:
            if column == "*":
                continue
            if column not in known:
                # The real Persistence_Layer's answer to a projection naming a column that does
                # not exist. Requirement 1.3 forbids the handler asking for one at all.
                raise _UndefinedColumn(self._table, column)

        rows = [dict(row) for row in self._fake.rows.get(self._table, [])]
        for column, value in self._filters:
            if isinstance(value, list):
                rows = [row for row in rows if row.get(column) in value]
            else:
                rows = [row for row in rows if row.get(column) == value]

        projected = [
            {name: row.get(name) for name in (self._columns or list(row))} for row in rows
        ]
        if self._single:
            return _Response(projected[0] if projected else None)
        return _Response(projected)


class _FakeSupabase:
    """A service-role client over in-memory rows, with a real column contract.

    Only the verbs the analytics reads use are implemented. A verb the handler starts using that
    is missing here fails loudly with ``AttributeError`` rather than silently returning a builder
    that ignores it — which is the failure mode a permissive ``__getattr__`` double has, and the
    reason there is none.
    """

    def __init__(self, **rows: List[Dict[str, Any]]) -> None:
        self.rows: Dict[str, List[Dict[str, Any]]] = {
            name: list(value) for name, value in rows.items()
        }
        self.selects: List[Tuple[str, Tuple[str, ...]]] = []
        self.tables_read: List[str] = []

    def table(self, name: str) -> _Query:
        self.tables_read.append(name)
        if name not in TABLE_COLUMNS:
            raise AssertionError(
                f"the analytics read touched {name!r}, a table this double declares no column "
                f"contract for; add it to TABLE_COLUMNS or stop reading it"
            )
        return _Query(self, name)

    def from_(self, name: str) -> _Query:
        return self.table(name)


class _FailingSupabase:
    """A client whose every read did not complete. One failure shape, freshly raised per call.

    A fresh instance per ``.execute()`` because re-raising one exception object across requests
    chains ``__context__`` from the previous request onto the next, turning a later traceback into
    a transcript of every earlier one.
    """

    def __init__(self) -> None:
        self.tables_read: List[str] = []

    def table(self, name: str) -> "_FailingQuery":
        self.tables_read.append(name)
        return _FailingQuery()

    def from_(self, name: str) -> "_FailingQuery":
        return self.table(name)


class _FailingVerb:
    def __init__(self, query: "_FailingQuery") -> None:
        self._query = query

    def __call__(self, *_a: Any, **_k: Any) -> "_FailingQuery":
        return self._query

    def __getattr__(self, name: str) -> Any:
        return getattr(self._query, name)


class _FailingQuery:
    """Builds without complaint; fails at the terminal ``.execute()``, where a read really fails."""

    def __getattr__(self, _name: str) -> _FailingVerb:
        return _FailingVerb(self)

    def execute(self) -> Any:
        raise ConnectionError(
            "could not connect to server: Connection refused\n\tis the server running on host "
            '"db.internal.vyomquant.io" and accepting TCP/IP connections on port 5432?'
        )


# ══════════════════════════════════════════════════════════════════════════
# Row builders
# ══════════════════════════════════════════════════════════════════════════


def _listing(
    listing_id: str,
    *,
    author_id: str = OWNER_ID,
    moderation_status: str = "approved",
    clone_count: int = 0,
    subscriber_count: int = 0,
    price: str = "19.99",
    price_minor: int = 1999,
    currency: str = "USD",
    avg_rating: Optional[float] = None,
    rating_count: int = 0,
) -> Dict[str, Any]:
    """One ``library_strategies`` row, carrying only columns that exist."""
    return {
        "id": listing_id,
        "name": f"listing-{listing_id}",
        "author_id": author_id,
        "moderation_status": moderation_status,
        "clone_count": clone_count,
        "subscriber_count": subscriber_count,
        "price": price,
        "price_minor": price_minor,
        "currency": currency,
        "avg_rating": avg_rating,
        "rating_count": rating_count,
        "published_at": "2025-01-01T00:00:00+00:00",
        "is_active": True,
    }


def _settlement(
    reference: str,
    *,
    owner_id: str = OWNER_ID,
    amount_minor: int,
    currency: str = "USD",
    is_reversal: bool = False,
) -> Dict[str, Any]:
    """One ``marketplace_settlements`` row, split exactly as ``money.split_ninety_ten`` does.

    The split is restated here with integer arithmetic rather than imported, because the row is
    *input* to this file: seeding it from the production function would make the oracle below
    agree with a wrong split. ``chk_settlement_conserved`` is satisfied by construction —
    ``platform_fee_minor`` is the residual, never a second percentage.
    """
    owner_share = (amount_minor * 90) // 100
    return {
        "id": f"stl-{reference}-{'r' if is_reversal else 'p'}",
        "subscription_id": f"sub-{reference}",
        "listing_id": "lst-1",
        "owner_id": owner_id,
        "purchaser_id": "33333333-3333-3333-3333-333333333333",
        "amount_minor": amount_minor,
        "owner_share_minor": owner_share,
        "platform_fee_minor": amount_minor - owner_share,
        "currency": currency,
        "provider": "stripe",
        "provider_reference": reference,
        "is_reversal": is_reversal,
        "reverses_reference": reference if is_reversal else None,
        "settled_at": "2025-06-15T12:00:00+00:00",
    }


# ══════════════════════════════════════════════════════════════════════════
# The oracle: Requirement 10.7's formula, over the SEEDED rows
# ══════════════════════════════════════════════════════════════════════════


def _totals_oracle(
    rows: Sequence[Mapping[str, Any]], *, owner_id: str
) -> Dict[str, Dict[str, int]]:
    """Per-currency ``owner``/``platform``/``gross`` totals for ``owner_id``, reversals subtracted.

    "The sum of ``owner_share`` over non-reversal Settlement_Records minus the sum of
    ``owner_share`` over reversal Settlement_Records", in exact integer Minor_Units, grouped by
    currency and never combined across currencies (Requirement 10.7). A plain loop over the seed
    data: it shares no code with the implementation, so an implementation that summed the wrong
    column or folded two currencies together disagrees with it.
    """
    totals: Dict[str, Dict[str, int]] = {}
    for row in rows:
        if row["owner_id"] != owner_id:
            continue
        sign = -1 if row["is_reversal"] else 1
        bucket = totals.setdefault(
            str(row["currency"]), {"owner": 0, "platform": 0, "gross": 0}
        )
        bucket["owner"] += sign * int(row["owner_share_minor"])
        bucket["platform"] += sign * int(row["platform_fee_minor"])
        bucket["gross"] += sign * int(row["amount_minor"])
    return totals


# ══════════════════════════════════════════════════════════════════════════
# Harness
# ══════════════════════════════════════════════════════════════════════════


def _run_coroutine(coro: Any) -> Any:
    """Run ``coro`` to completion **without leaving the thread without an event loop**.

    Not ``asyncio.run``: that closes its loop and then calls ``set_event_loop(None)``, leaving the
    main thread with *no* current loop, which raises ``RuntimeError`` in the neighbouring suites
    that still call ``asyncio.get_event_loop()``. Lifted from
    ``tests/test_checkout_service.py::_run``, where the reasoning was first recorded.
    """
    previous: Optional[asyncio.AbstractEventLoop]
    try:
        previous = asyncio.get_event_loop_policy().get_event_loop()
    except RuntimeError:
        previous = None

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        if previous is not None and not previous.is_closed():
            asyncio.set_event_loop(previous)
        else:
            asyncio.set_event_loop(asyncio.new_event_loop())


class _Answer:
    """What the endpoint answered, and which tables it touched getting there."""

    def __init__(self, status_code: int, text: str, body: Any, tables_read: Tuple[str, ...]):
        self.status_code = status_code
        self.text = text
        self.body = body
        self.tables_read = tables_read


def _get_analytics(db: Any, *, caller: Optional[Dict[str, Any]] = None) -> _Answer:
    """Drive ``GET /api/library/creator/analytics`` against ``db`` as an authenticated creator."""
    app.dependency_overrides[get_current_user] = lambda: dict(caller or OWNER)
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(library_router, "_build_service_client", lambda: db)
            patch.setattr(library_router, "_get_service_client", lambda: db)
            response = client.get(ANALYTICS_PATH)
    finally:
        app.dependency_overrides.clear()

    try:
        body = response.json()
    except ValueError:  # pragma: no cover - a non-JSON body is itself a finding
        body = None
    return _Answer(
        response.status_code,
        response.text,
        body,
        tuple(getattr(db, "tables_read", ())),
    )


def _numeric_leaves(body: Any, path: str = "", key: Optional[str] = None) -> List[str]:
    """Every numeric leaf in ``body`` as ``path=value``, excluding the error envelope's own echo.

    ``bool`` is not counted: a flag is not a figure, and Python makes ``bool`` an ``int``
    subclass. ``status_code`` is the one permitted numeric key — the envelope echoing the HTTP
    status it is already sending, which is not derived from a read. The same rule
    ``tests/test_marketplace_error_surface.py`` applies, restated here so this file stands alone.
    """
    permitted = {"status_code"}
    found: List[str] = []
    if isinstance(body, dict):
        for name, value in body.items():
            found.extend(_numeric_leaves(value, f"{path}.{name}", str(name)))
    elif isinstance(body, list):
        for index, value in enumerate(body):
            found.extend(_numeric_leaves(value, f"{path}[{index}]", key))
    elif isinstance(body, bool):
        pass
    elif isinstance(body, (int, float)):
        if key not in permitted:
            found.append(f"{path or '<root>'}={body!r}")
    return found


def _all_numbers(body: Any) -> List[Any]:
    """Every numeric leaf value anywhere in ``body``, ``bool`` excluded."""
    values: List[Any] = []
    if isinstance(body, dict):
        for value in body.values():
            values.extend(_all_numbers(value))
    elif isinstance(body, list):
        for value in body:
            values.extend(_all_numbers(value))
    elif isinstance(body, bool):
        pass
    elif isinstance(body, (int, float)):
        values.append(body)
    return values


def _keys_anywhere(body: Any) -> Set[str]:
    """Every mapping key at any depth in ``body``."""
    keys: Set[str] = set()
    if isinstance(body, dict):
        for name, value in body.items():
            keys.add(str(name))
            keys |= _keys_anywhere(value)
    elif isinstance(body, list):
        for value in body:
            keys |= _keys_anywhere(value)
    return keys


# ══════════════════════════════════════════════════════════════════════════
# The handler's own source, for the structural claims
# ══════════════════════════════════════════════════════════════════════════


def _handler_source(name: str) -> Any:
    """The ``ast`` node of one handler in ``library.py``, read from disk.

    From disk rather than through ``inspect``, so no import side effect is needed and so a
    decorator that wrapped the function cannot hide its body.
    """
    tree = ast.parse(LIBRARY_ROUTER_SOURCE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} is not defined in {LIBRARY_ROUTER_SOURCE}")


def _handler_code(name: str) -> str:
    """One handler's executable body as source text, **with its docstring removed**.

    The docstring is stripped because these assertions are about what the handler *references*,
    and prose references nothing. A docstring that explains why ``rating_average`` was deleted
    would otherwise read as evidence that it is still selected — the assertion would fire on the
    explanation of its own fix, which is a false positive, not a stricter test. ``ast.unparse``
    already discards comments, so the docstring is the only prose that survives.
    """
    node = _handler_source(name)
    body = list(node.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    assert body, f"{name} has no body other than its docstring"
    return "\n".join(ast.unparse(statement) for statement in body)


def _handler_code_nodes(name: str) -> List[ast.AST]:
    """Every ``ast`` node in one handler's executable body, its docstring excluded."""
    node = _handler_source(name)
    body = list(node.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    nodes: List[ast.AST] = []
    for statement in body:
        nodes.extend(ast.walk(statement))
    return nodes


ANALYTICS_HANDLERS = ("creator_analytics", "subscriber_analytics")

#: Substrings that make a name or a string literal MONEY-BEARING. An expression mentioning one of
#: these is on a money path, and Requirements 10.3 and 8.13 forbid binary floating point there.
#:
#: Scoped this way rather than as a blanket ban on ``float`` anywhere in the handler, because the
#: handler legitimately reports a *rating* — a ``NUMERIC(3,2)`` that is not an amount, is never
#: summed into a total and is never charged to anybody. Banning ``float`` on the rating too would
#: not make the money safer; it would only make the rule easy to dismiss. What is NOT relaxed: a
#: float literal anywhere in either handler is refused outright by
#: :meth:`TestNoFabricatedFigure.test_no_float_literal_appears_at_all`, because ``0.0``, ``0.90``
#: and ``0.10`` — every float literal these two handlers ever carried — were money.
MONEY_BEARING_SUBSTRINGS: Tuple[str, ...] = (
    "price",
    "amount",
    "minor",
    "earning",
    "mrr",
    "revenue",
    "fee",
    "spend",
    "paid",
    "payout",
    "total_earnings",
    "settlement",
)


def _mentions_money(node: ast.AST) -> Optional[str]:
    """The money-bearing token ``node`` mentions, or ``None``.

    Names, attributes and string literals are all inspected: the pre-fix defect reached its money
    through ``s.get("monthly_price")``, so the amount's identity lived in a *string*, not in an
    identifier.
    """
    for child in ast.walk(node):
        candidates: List[str] = []
        if isinstance(child, ast.Name):
            candidates.append(child.id)
        elif isinstance(child, ast.Attribute):
            candidates.append(child.attr)
        elif isinstance(child, ast.Constant) and isinstance(child.value, str):
            candidates.append(child.value)
        for candidate in candidates:
            lowered = candidate.lower()
            for token in MONEY_BEARING_SUBSTRINGS:
                if token in lowered:
                    return candidate
    return None


# ══════════════════════════════════════════════════════════════════════════
# 1. The endpoint is reachable at all (task 13.1's fix, re-checked here)
# ══════════════════════════════════════════════════════════════════════════


class TestReachable:
    """Before any claim about the *body*, the endpoint has to be the one that runs.

    ``GET /creator/{creator_id}`` was declared before ``GET /creator/analytics``, and FastAPI
    resolves in registration order, so the literal path was permanently swallowed and answered
    **422** from ``_safe_uuid("analytics", "creator_id")``. Task 13.1 spliced the literal routes
    in front. Confirming that here is not duplication of
    ``tests/test_library_route_resolution.py``: without it a 422 below would be misread as a
    defect in ``creator_analytics`` rather than in the route table.
    """

    def test_the_literal_path_resolves_to_creator_analytics(self) -> None:
        scope = {
            "type": "http",
            "method": "GET",
            "path": ANALYTICS_PATH,
            "root_path": "",
            "headers": [],
            "query_string": b"",
        }
        for route in app.router.routes:
            match, _child = route.matches(scope)
            if match == Match.FULL:
                resolved = getattr(route, "name", None) or route.endpoint.__name__
                assert resolved == "creator_analytics", (
                    f"GET {ANALYTICS_PATH} resolves to {resolved!r}. "
                    f"GET /creator/{{creator_id}} is being matched first again, so the endpoint "
                    f"answers 422 from _safe_uuid('analytics', 'creator_id')."
                )
                return
        pytest.fail(f"GET {ANALYTICS_PATH} resolves to no route at all")

    def test_a_successful_read_is_not_answered_422(self) -> None:
        """The observable half of the same claim, driven through the app."""
        db = _FakeSupabase(
            library_strategies=[_listing("lst-1")],
            marketplace_settlements=[],
        )
        answer = _get_analytics(db)
        assert answer.status_code != 422, (
            f"GET {ANALYTICS_PATH} answered 422 — the shadowed-route defect task 13.1 fixed. "
            f"Body: {answer.text[:300]!r}"
        )
        assert answer.status_code == 200, (
            f"a read that completed must be answered 200, got {answer.status_code}. "
            f"Body: {answer.text[:300]!r}"
        )


# ══════════════════════════════════════════════════════════════════════════
# 2. A failed read refuses — it does not answer 200 with zeros
#    (Requirements 1.5, 1.7 — the defect this file exists for)
# ══════════════════════════════════════════════════════════════════════════


#: Every key that names a FIGURE in one of the two analytics bodies. Presence in a read-failure
#: body is a fabrication whatever the value: ``0.0`` invents an amount and ``null`` invents the
#: claim that an amount was read and was empty (Requirement 28.5's omitted-versus-null
#: distinction). The pre-21.1 handler returned SEVEN of these on a failed read.
FABRICATED_FIGURE_KEYS: FrozenSet[str] = frozenset(
    {
        "total_earnings_usd",
        "monthly_recurring_revenue",
        "platform_fee_paid",
        "active_subscribers",
        "published_strategies_count",
        "rating_average",
        "avg_rating",
        "rating_count",
        "subscriber_count",
        "owner_earnings_minor",
        "platform_fee_minor",
        "gross_minor",
        "monthly_spend_usd",
        "active_subscriptions_count",
        "total_subscriptions_count",
    }
)


class TestFailedReadRefuses:
    """A Marketplace read that did not complete answers 500/503 with a stable code and no figures.

    This is the class that fails hardest against the pre-21.1 handler: its
    ``except Exception: return {…zeros…}`` answers **200** with every figure ``0.0``.
    """

    def test_a_failed_read_is_not_answered_200(self) -> None:
        answer = _get_analytics(_FailingSupabase())
        assert answer.status_code in (500, 503), (
            f"a read that did not complete was answered {answer.status_code}. Requirement 1.5 "
            f"admits only 500 or 503 — never a success body, and never a zero-filled one. "
            f"Body: {answer.text[:400]!r}"
        )

    def test_a_failed_read_carries_the_stable_read_failed_code(self) -> None:
        answer = _get_analytics(_FailingSupabase())
        body = answer.body if isinstance(answer.body, dict) else {}
        error = body.get("error") if isinstance(body.get("error"), dict) else {}
        assert error.get("code") == errors.MARKETPLACE_READ_FAILED, (
            f"a failed read must carry the stable {errors.MARKETPLACE_READ_FAILED!r} code so a "
            f"client can branch on it. Body: {answer.text[:400]!r}"
        )

    def test_a_failed_read_carries_no_figure_key(self) -> None:
        answer = _get_analytics(_FailingSupabase())
        leaked = sorted(_keys_anywhere(answer.body) & FABRICATED_FIGURE_KEYS)
        assert not leaked, (
            f"a failed read returned a body carrying the figure key(s) {leaked}; Requirement 1.7 "
            f"forbids any numeric field that was not read. Body: {answer.text[:400]!r}"
        )

    def test_a_failed_read_carries_no_numeric_payload_at_all(self) -> None:
        answer = _get_analytics(_FailingSupabase())
        numbers = _numeric_leaves(answer.body)
        assert not numbers, (
            f"a failed read returned the numeric payload {numbers}; a refusal carries a code and "
            f"a sentence, not figures. Body: {answer.text[:400]!r}"
        )

    def test_a_failed_read_really_did_attempt_the_read(self) -> None:
        """A guard on the guard: an endpoint that refused *before* touching the Persistence_Layer
        would satisfy every assertion above while proving nothing about a failed read."""
        db = _FailingSupabase()
        _get_analytics(db)
        assert db.tables_read, (
            "the endpoint answered without attempting a single read against the failing client, "
            "so this class cannot claim to cover a read failure"
        )

    def test_the_projection_names_only_columns_that_exist(self) -> None:
        """The specific failure the two non-existent columns caused: PostgreSQL ``42703``.

        Driven by seeding a listing row and letting :class:`_FakeSupabase`'s column contract
        answer the projection. A handler that still asks for ``monthly_price`` or
        ``rating_average`` provokes a real 42703 here — and because the pre-21.1 handler swallows
        it into a zero-filled 200, the status alone cannot tell the two apart. So what is asserted
        is that the body is the *answered* one: figures that came from a read, not a body the
        ``except`` clause assembled.
        """
        db = _FakeSupabase(
            library_strategies=[_listing("lst-1")],
            marketplace_settlements=[_settlement("pi_1", amount_minor=1999)],
        )
        answer = _get_analytics(db)
        assert answer.status_code == 200, (
            f"the analytics read named a column that does not exist on library_strategies "
            f"(PostgreSQL 42703) — the condition migration 007's header records for /trending "
            f"and /featured. Requirement 1.4 forbids referencing monthly_price and "
            f"rating_average at all. Status {answer.status_code}, body {answer.text[:400]!r}"
        )
        assert "library_strategies" in db.tables_read, (
            "the handler never read library_strategies at all"
        )
        for table, columns in db.selects:
            unknown = sorted(set(columns) - set(TABLE_COLUMNS[table]) - {"*"})
            assert not unknown, (
                f"the {table} projection names {unknown}, which the applied migration set does "
                f"not create (Requirements 1.3, 1.4)"
            )
        assert _reported_earnings(answer.body)["USD"]["owner_earnings_minor"] == 1799, (
            f"the body is not the answered one — the figures did not come from the ledger read. "
            f"Body: {answer.text[:400]!r}"
        )

    def test_the_error_body_echoes_no_driver_internal(self) -> None:
        """Requirement 22.9: the sqlstate, the column name, the query and the internal host all
        reach the handler; none may come back out."""
        answer = _get_analytics(_FailingSupabase())
        lowered = answer.text.lower()
        for internal in (
            "42703",
            "monthly_price",
            "library_strategies",
            "could not connect",
            "db.internal.vyomquant.io",
            "5432",
            "traceback",
        ):
            assert internal.lower() not in lowered, (
                f"the refusal body echoes the driver's own {internal!r}: {answer.text[:400]!r}"
            )


# ══════════════════════════════════════════════════════════════════════════
# 3. The reported total IS the ledger sum (Requirements 10.6, 10.7)
# ══════════════════════════════════════════════════════════════════════════


def _reported_earnings(body: Any) -> Dict[str, Dict[str, int]]:
    """The per-currency earnings the response reports, keyed by currency.

    The response carries one entry per currency rather than one blended figure, because
    Requirement 10.7 forbids combining Settlement_Records of different currencies into a single
    total. A body that reported one currency-less number could not satisfy that, and this reader
    fails loudly rather than guessing which currency such a number meant.
    """
    assert isinstance(body, dict), f"expected an object body, got {type(body).__name__}"
    entries = body.get("earnings")
    assert isinstance(entries, list), (
        f"the response carries no per-currency 'earnings' list, so a reader cannot tell which "
        f"currency any total is in (Requirement 10.7). Body keys: {sorted(body)}"
    )
    reported: Dict[str, Dict[str, int]] = {}
    for entry in entries:
        assert isinstance(entry, dict), f"an earnings entry is not an object: {entry!r}"
        currency = entry.get("currency")
        assert isinstance(currency, str) and currency, (
            f"an earnings entry carries no currency: {entry!r}"
        )
        assert currency not in reported, f"{currency} is reported twice: {entries!r}"
        reported[currency] = {
            key: value for key, value in entry.items() if key != "currency"
        }
    return reported


class TestEarningsAreLedgerSums:
    """Every reported figure is the persisted ledger, summed — per currency, reversals subtracted.

    This is the claim that makes Requirement 10.6 observable at the HTTP boundary. The quantity
    asserted is the same one
    ``tests/property/test_settlement_ledger.py::test_p5_reported_totals_equal_ledger_sums``
    states as a property over generated sequences; what is added here is that the *endpoint*
    reports it.
    """

    def test_the_owner_total_equals_the_per_currency_ledger_sum(self) -> None:
        settlements = [
            _settlement("pi_usd_1", amount_minor=1999, currency="USD"),
            _settlement("pi_usd_2", amount_minor=4999, currency="USD"),
            _settlement("pi_usd_3", amount_minor=101, currency="USD"),
            _settlement("pi_inr_1", amount_minor=249900, currency="INR"),
            # A reversal at the full recorded amount: Requirement 10.7 SUBTRACTS it.
            _settlement("pi_usd_2", amount_minor=4999, currency="USD", is_reversal=True),
            # Another creator's money. Unreachable: the identity is a predicate on the read.
            _settlement("pi_other", amount_minor=999999, owner_id=OTHER_OWNER_ID),
        ]
        db = _FakeSupabase(
            library_strategies=[_listing("lst-1", subscriber_count=7, clone_count=13)],
            marketplace_settlements=settlements,
        )
        answer = _get_analytics(db)
        assert answer.status_code == 200, answer.text[:400]

        expected = _totals_oracle(settlements, owner_id=OWNER_ID)
        reported = _reported_earnings(answer.body)

        assert set(reported) == set(expected), (
            f"the response reports currencies {sorted(reported)}; the ledger holds "
            f"{sorted(expected)} for this owner"
        )
        for currency, totals in expected.items():
            entry = reported[currency]
            owner_reported = entry.get("owner_earnings_minor")
            assert isinstance(owner_reported, int) and not isinstance(owner_reported, bool), (
                f"the {currency} owner total must be an int number of Minor_Units, got "
                f"{type(owner_reported).__name__}: {owner_reported!r}"
            )
            assert owner_reported == totals["owner"], (
                f"the reported {currency} owner total is {owner_reported} Minor_Units; the exact "
                f"integer sum of owner_share_minor over non-reversal rows minus reversal rows is "
                f"{totals['owner']} — a discrepancy of {owner_reported - totals['owner']} "
                f"(Requirements 10.6, 10.7)"
            )
            assert entry.get("platform_fee_minor") == totals["platform"], (
                f"the reported {currency} platform total is {entry.get('platform_fee_minor')}; "
                f"the ledger sum of platform_fee_minor is {totals['platform']}"
            )
            assert entry.get("gross_minor") == totals["gross"], (
                f"the reported {currency} gross is {entry.get('gross_minor')}; the ledger sum of "
                f"amount_minor is {totals['gross']}"
            )

    def test_currencies_are_never_combined(self) -> None:
        """Two currencies, one owner: neither total may contain the other's Minor_Units."""
        settlements = [
            _settlement("pi_usd", amount_minor=10_000, currency="USD"),
            _settlement("pi_inr", amount_minor=10_000, currency="INR"),
        ]
        db = _FakeSupabase(
            library_strategies=[_listing("lst-1")],
            marketplace_settlements=settlements,
        )
        answer = _get_analytics(db)
        assert answer.status_code == 200, answer.text[:400]
        reported = _reported_earnings(answer.body)

        assert sorted(reported) == ["INR", "USD"], (
            f"both currencies must be reported separately, got {sorted(reported)}"
        )
        assert reported["USD"]["owner_earnings_minor"] == 9_000
        assert reported["INR"]["owner_earnings_minor"] == 9_000
        combined = 18_000
        for currency, entry in reported.items():
            assert entry["owner_earnings_minor"] != combined, (
                f"the {currency} total is the sum of BOTH currencies' Minor_Units; "
                f"Requirement 10.7 forbids combining them"
            )

    def test_no_figure_is_a_counter_multiplied_by_a_price(self) -> None:
        """Requirement 10.6, behaviourally.

        ``clone_count``, ``subscriber_count`` and ``price`` are seeded so that every product a
        counter-times-price derivation could produce is a distinctive number appearing nowhere in
        an honest response. The ledger is seeded with a *different* amount, so the honest total
        cannot collide with a fabricated one by accident.
        """
        clone_count = 37
        subscriber_count = 41
        price_minor = 1_300  # 13.00 major units
        forbidden = {
            clone_count * price_minor,          # 48_100
            subscriber_count * price_minor,     # 53_300
            (clone_count * price_minor * 90) // 100,
            (subscriber_count * price_minor * 90) // 100,
            clone_count * 1300,
            subscriber_count * 1300,
        }
        db = _FakeSupabase(
            library_strategies=[
                _listing(
                    "lst-1",
                    clone_count=clone_count,
                    subscriber_count=subscriber_count,
                    price="13.00",
                    price_minor=price_minor,
                )
            ],
            marketplace_settlements=[
                _settlement("pi_only", amount_minor=777, currency="USD")
            ],
        )
        answer = _get_analytics(db)
        assert answer.status_code == 200, answer.text[:400]

        reported = _reported_earnings(answer.body)
        assert reported["USD"]["owner_earnings_minor"] == (777 * 90) // 100

        offenders = sorted(
            value for value in _all_numbers(answer.body) if value in forbidden
        )
        assert not offenders, (
            f"the response carries {offenders}, each a product of a counter and a price. "
            f"Requirement 10.6 forbids deriving an earnings figure from clone_count, "
            f"subscriber_count or any counter multiplied by a current price. "
            f"Body: {answer.text[:400]!r}"
        )

    def test_an_empty_ledger_reports_no_currency_rather_than_a_zero(self) -> None:
        """A read that completed and found nothing is not the same as a read that failed.

        An owner with no Settlement_Records has no per-currency total — there is no currency to
        state one in. Reporting an empty list is truthful; inventing ``USD 0`` would be a figure
        no row supports (Requirement 28.5).
        """
        db = _FakeSupabase(
            library_strategies=[_listing("lst-1")],
            marketplace_settlements=[],
        )
        answer = _get_analytics(db)
        assert answer.status_code == 200, answer.text[:400]
        assert _reported_earnings(answer.body) == {}, (
            "an owner with no settlements must be reported with no per-currency total, not with "
            "a fabricated zero in a currency nothing was ever paid in"
        )

    def test_another_creators_earnings_are_unreachable(self) -> None:
        """The identity comes from the authenticated session and travels as a predicate.

        Both owners' rows are in the ledger; the authenticated caller may see only their own, and
        the read must be filtered in the Persistence_Layer rather than in Python after the fact.
        """
        settlements = [
            _settlement("pi_mine", amount_minor=1_000, owner_id=OWNER_ID),
            _settlement("pi_theirs", amount_minor=9_999_999, owner_id=OTHER_OWNER_ID),
        ]
        db = _FakeSupabase(
            library_strategies=[_listing("lst-1")],
            marketplace_settlements=settlements,
        )
        answer = _get_analytics(db)
        assert answer.status_code == 200, answer.text[:400]

        reported = _reported_earnings(answer.body)
        assert reported["USD"]["owner_earnings_minor"] == 900, (
            f"the caller was reported {reported['USD']['owner_earnings_minor']} Minor_Units; "
            f"only their own 1000-Minor_Unit payment is theirs"
        )
        assert (9_999_999 * 90) // 100 not in _all_numbers(answer.body), (
            "another creator's owner share appears in the response"
        )

    def test_the_earnings_read_is_predicated_on_the_owner_in_the_query(self) -> None:
        """Not filtered in Python afterwards: the predicate is on the read itself.

        Asserted from the handler's source, because a filter applied after the rows arrive still
        pulls every owner's ledger across the boundary — which RLS and Requirement 21.4 both
        exist to prevent, and which no response-body assertion can distinguish.
        """
        earnings_source = _earnings_read_source()
        assert 'eq("owner_id"' in earnings_source or "eq('owner_id'" in earnings_source, (
            "the marketplace_settlements read is not constrained by owner_id as a predicate on "
            "the query, so it reads every creator's ledger and filters afterwards"
        )


def _earnings_read_source() -> str:
    """The source of whatever function issues the ``marketplace_settlements`` read.

    The read may legitimately live in ``settlement_service`` (beside ``find_settled_payment``,
    which is where a ledger read belongs) rather than in the router, so both are searched and the
    module that actually issues it is the one whose source is returned. A read that exists in
    neither is itself the failure.
    """
    candidates = [
        LIBRARY_ROUTER_SOURCE,
        REPO_ROOT / "backend_app" / "backend" / "marketplace" / "settlement_service.py",
    ]
    for path in candidates:
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            rendered = ast.unparse(node)
            if "marketplace_settlements" not in rendered and "SETTLEMENT_TABLE" not in rendered:
                continue
            if "owner_id" in rendered and "select" in rendered:
                return rendered
    raise AssertionError(
        "no function in routers/library.py or marketplace/settlement_service.py issues a "
        "marketplace_settlements read predicated on owner_id; the earnings figure therefore "
        "cannot be a sum over persisted Settlement_Records (Requirement 10.6)"
    )


# ══════════════════════════════════════════════════════════════════════════
# 4. The deleted constructs stay deleted
# ══════════════════════════════════════════════════════════════════════════


class TestNoFabricatedFigure:
    """The five constructs task 21.1 deletes, asserted structurally so they cannot creep back.

    A body assertion cannot see a ``float`` that happens to be exact for the values a test chose,
    nor a bare ``except`` that no test input reaches. These do.
    """

    @pytest.mark.parametrize("handler", ANALYTICS_HANDLERS)
    def test_neither_non_existent_column_is_referenced(self, handler: str) -> None:
        """Requirement 1.4: ``monthly_price`` and ``rating_average`` exist on no table here."""
        source = _handler_code(handler)
        for column in ("monthly_price", "rating_average"):
            assert column not in source, (
                f"{handler} still references {column!r}, which does not exist on "
                f"library_strategies; the query fails with PostgreSQL 42703 (Requirement 1.4)"
            )

    @pytest.mark.parametrize("handler", ANALYTICS_HANDLERS)
    def test_no_float_or_round_appears_on_a_money_expression(self, handler: str) -> None:
        """Requirement 10.3: no money arithmetic in binary floating point.

        Both deleted expressions are caught by name: ``float(s.get("monthly_price") or 0.0)``
        mentions ``monthly_price``, ``round(mrr * 0.90, 2)`` mentions ``mrr``, and
        ``float(s.get("price_paid") or 0.0)`` mentions ``price_paid``. ``round`` is refused on a
        money expression for the same reason as ``float``: needing to round a money figure means
        the arithmetic above it was not exact, and an exact integer never needs it.
        """
        for child in _handler_code_nodes(handler):
            if not isinstance(child, ast.Call):
                continue
            callee = child.func
            name = (
                callee.id if isinstance(callee, ast.Name) else getattr(callee, "attr", None)
            )
            if name not in ("float", "round"):
                continue
            mentioned = _mentions_money(child)
            assert mentioned is None, (
                f"{handler} computes {ast.unparse(child)!r}, which applies {name}() to the "
                f"money-bearing {mentioned!r}. Every amount is an exact integer number of "
                f"Minor_Units end to end (Requirement 10.3)"
            )

    @pytest.mark.parametrize("handler", ANALYTICS_HANDLERS)
    def test_no_float_literal_appears_at_all(self, handler: str) -> None:
        """Every float literal these two handlers ever carried was money.

        ``0.0`` (the seven fabricated zeros), ``0.90`` (the owner's share) and ``0.10`` (the
        platform's) — so a float literal here is refused outright rather than only where a
        money-bearing name happens to sit beside it. The 90/10 split is
        ``money.split_ninety_ten``'s integer arithmetic, computed once at settlement time; there
        is no percentage left in this file to write as a decimal.
        """
        for child in _handler_code_nodes(handler):
            if isinstance(child, ast.Constant) and isinstance(child.value, float):
                pytest.fail(
                    f"{handler} carries the float literal {child.value!r}; every money value is "
                    f"an int number of Minor_Units and the split is integer arithmetic in "
                    f"money.split_ninety_ten (Requirements 10.1, 10.3)"
                )

    @pytest.mark.parametrize("handler", ANALYTICS_HANDLERS)
    def test_no_counter_is_multiplied_by_anything(self, handler: str) -> None:
        """Requirement 10.6, structurally: no ``clone_count * price`` shape survives."""
        for child in _handler_code_nodes(handler):
            if not isinstance(child, ast.BinOp) or not isinstance(child.op, ast.Mult):
                continue
            rendered = ast.unparse(child)
            for counter in ("clone_count", "subscriber_count", "price"):
                assert counter not in rendered, (
                    f"{handler} computes {rendered!r} — an earnings figure derived from a "
                    f"counter multiplied by a price, which Requirement 10.6 forbids"
                )

    @pytest.mark.parametrize("handler", ANALYTICS_HANDLERS)
    def test_no_bare_except_returns_a_body(self, handler: str) -> None:
        """Requirement 30.5: no broad ``except`` on a correctness path without a defined outcome.

        A broad ``except`` is admitted only when it *re-raises* — the convention the rest of
        ``library.py`` follows, where the handler logs the driver detail and raises
        ``MarketplaceError(MARKETPLACE_READ_FAILED)``. A broad ``except`` that ``return``s is the
        swallow, and a zero-filled body is what it returned.
        """
        for child in _handler_code_nodes(handler):
            if not isinstance(child, ast.ExceptHandler):
                continue
            caught = ast.unparse(child.type) if child.type is not None else "<bare>"
            if caught not in ("<bare>", "Exception", "BaseException"):
                continue
            returns = [
                grandchild
                for grandchild in ast.walk(child)
                if isinstance(grandchild, ast.Return)
            ]
            assert not returns, (
                f"{handler} has an `except {caught}` that returns a body instead of raising a "
                f"defined error outcome. A read failure answered with a success body is the "
                f"fabrication Requirements 1.5, 1.7 and 30.5 forbid: "
                f"{ast.unparse(returns[0])!r}"
            )

    def test_the_invented_payout_schedule_is_gone(self) -> None:
        """No payout mechanism exists, so Requirement 28 forbids presenting one."""
        source = _handler_code("creator_analytics")
        assert "payout_schedule" not in source, (
            "creator_analytics still returns payout_schedule; the string "
            '"Monthly auto-transfer (Stripe Connect)" describes a mechanism that does not exist '
            "(Requirements 28.1, 28.2)"
        )

        db = _FakeSupabase(
            library_strategies=[_listing("lst-1")],
            marketplace_settlements=[],
        )
        answer = _get_analytics(db)
        assert answer.status_code == 200, answer.text[:400]
        assert "payout_schedule" not in _keys_anywhere(answer.body), (
            f"the response still carries payout_schedule: {answer.text[:300]!r}"
        )

    def test_no_evaluation_score_reaches_an_analytics_response(self) -> None:
        """``evaluation_score`` is in ``listing_projection.DENIED_LISTING_COLUMNS``; an owner-scoped
        analytics response is not a licence to leak it either."""
        db = _FakeSupabase(
            library_strategies=[_listing("lst-1")],
            marketplace_settlements=[_settlement("pi_1", amount_minor=1999)],
        )
        answer = _get_analytics(db)
        assert answer.status_code == 200, answer.text[:400]
        assert "evaluation_score" not in answer.text, (
            f"an internal evaluation score reached the analytics response: {answer.text[:300]!r}"
        )


# ══════════════════════════════════════════════════════════════════════════
# 5. The rating is reported as ABSENT, not as a zero (Requirement 6.9)
# ══════════════════════════════════════════════════════════════════════════


class TestRatingAbsentWhenUnrated:
    """An omitted field and a null field mean different things (Requirement 28.5).

    The pre-21.1 handler answered ``rating_average: 0.0`` for a creator nobody had rated, which
    reads as "rated, and badly" rather than "not yet rated".
    """

    def test_an_unrated_creator_has_no_rating_field_at_all(self) -> None:
        db = _FakeSupabase(
            library_strategies=[_listing("lst-1", avg_rating=None, rating_count=0)],
            marketplace_settlements=[],
        )
        answer = _get_analytics(db)
        assert answer.status_code == 200, answer.text[:400]
        keys = _keys_anywhere(answer.body)
        assert "avg_rating" not in keys and "rating_average" not in keys, (
            f"a creator with rating_count = 0 must have the rating OMITTED, not reported as a "
            f"substitute value (Requirements 6.9, 28.5). Body: {answer.text[:300]!r}"
        )

    def test_a_rated_creator_has_the_persisted_rating(self) -> None:
        db = _FakeSupabase(
            library_strategies=[_listing("lst-1", avg_rating=4.5, rating_count=8)],
            marketplace_settlements=[],
        )
        answer = _get_analytics(db)
        assert answer.status_code == 200, answer.text[:400]
        assert isinstance(answer.body, dict)
        assert answer.body.get("rating_count") == 8, (
            f"the persisted rating_count must be reported as read: {answer.text[:300]!r}"
        )
        assert answer.body.get("avg_rating") == pytest.approx(4.5), (
            f"the persisted avg_rating must be reported as read: {answer.text[:300]!r}"
        )
