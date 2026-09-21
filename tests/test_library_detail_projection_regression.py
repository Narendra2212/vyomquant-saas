"""
tests/test_library_detail_projection_regression.py

Task 16.3 of ``marketplace-subscriptions-paper-trading``: the regression guard on the public
Listing projection — the detail read ``GET /api/library/{id}`` (task 16.1) and every catalogue
path task 16.2 repointed at the same serialiser.

WHAT IS ASSERTED (Requirements 6.4, 6.5)
----------------------------------------
1. ``GET /api/library/{id}`` carries NONE of the denied columns the task names —
   ``source_strategy_id``, ``moderation_notes``, ``moderated_by``, ``moderated_at``,
   ``evaluation_score``, ``deployment_requirements``, ``version_history``,
   ``equity_curve_snapshot``, ``risk_stop_loss_pct``, ``risk_take_profit_pct``,
   ``risk_max_position_size``, ``risk_max_drawdown_pct`` and ``has_ml_model`` — at ANY nesting
   depth (Requirement 6.4).
2. ``author_id``, any email address and any authentication identity never appear, as a key or a
   value, at any nesting depth (Requirement 6.5).
3. The creator is represented by a display alias only.
4. The same three statements hold for every path task 16.2 repointed — ``browse_library``,
   ``get_featured_strategies``, ``get_trending_strategies``, ``get_creator_profile``,
   ``compare_strategies``, ``get_recommendations`` and ``get_user_favorites`` — for BOTH an
   authenticated non-owner and an anonymous caller. A denied column reaching a catalogue card is
   the same defect as it reaching the detail body.

═══════════════════════════════════════════════════════════════════════════════════════════════
REVERT-PROOF — the exact pre-fix construct this module detects (Task 35.2 reads this section)
═══════════════════════════════════════════════════════════════════════════════════════════════
A regression test that passes against the unfixed code is evidence of nothing. The construct
this module is built to catch is the ONE that ``get_library_detail`` had before task 16.1, and
that every catalogue read had before task 16.2:

    resp = (svc.table("library_strategies")
              .select("*")                    # ← default-OPEN column list
              .eq("id", lib_id)
              . … .single().execute())
    detail = dict(resp.data)                  # ← the row IS the response body
    detail.pop("author_id", None)             # ← one hand-maintained redaction
    return detail

Three parts, all three named mechanically by :data:`PRE_FIX_CONSTRUCT`:

* ``.select("*")``          — the query asks for every column, including columns no migration
                              has written yet.
* ``dict(resp.data)``       — the driver row is serialised wholesale instead of a fresh dict
                              being built field by field.
* ``pop("author_id")``      — a deny-by-enumeration redaction, which by construction cannot
                              cover a column added later.

The fix is ``listing_projection.LISTING_SELECT`` (explicit column list) plus
``listing_projection.project_listing`` (explicit per-field assignment closing with
``assert set(out) <= PUBLIC_LISTING_FIELDS``).

HOW TO REPRODUCE THE REVERT, AND WHAT FAILS
-------------------------------------------
Task 35.2 does not need to hand-edit anything: the revert is executed mechanically, in-process,
by :data:`PRE_FIX_PROJECTION` — a callable with ``project_listing``'s signature that performs
exactly ``dict(row)`` + ``pop("author_id")``. Because task 16.2 routed EVERY public read through
the one serialiser, patching it reverts all eight paths at once.
``test_every_public_path_leaks_under_the_pre_fix_projection`` asserts each path then leaks,
naming the columns; the source guard
``test_the_pre_fix_construct_is_absent_from_get_library_detail`` asserts the three constructs
above are absent from the handler's own source.

For the record, the by-hand revert (``.select("*")`` in ``get_library_detail`` plus
``detail = dict(row); detail.pop("author_id", None)`` in place of the ``project_listing`` call)
was executed against this file while task 16.3 was implemented. It produced:

    AssertionError: GET /api/library/{id} leaked denied column(s)
    ['deployment_requirements', 'equity_curve_snapshot', 'evaluation_score', 'has_ml_model',
     'moderated_at', 'moderated_by', 'moderation_notes', 'risk_max_drawdown_pct',
     'risk_max_position_size', 'risk_stop_loss_pct', 'risk_take_profit_pct',
     'source_strategy_id', 'version_history']
    AssertionError: identity value 'creator.secret@private-identity.example' appears in
    GET /api/library/{id}
    AssertionError: the creator must be represented by the resolved display alias 'QuantWizard'

— all thirteen columns of the task's list, plus the email and the alias. 3 failed, 2 passed.

NON-VACUITY
-----------
Every seeded ``library_strategies`` row CARRIES a value in every denied column, plus an
``author_id`` and two email columns, so "the response has none of them" is a statement about a
row that HAS them. The same rows carry every column ``LISTING_SELECT`` requests, so
``project_listing`` succeeds rather than 503-ing.

HARNESS
-------
FastAPI ``TestClient`` plus a FILTER-AWARE fake Supabase double that deliberately IGNORES the
``.select(...)`` column list. Ignoring it is what makes the revert detectable: a fake that
honoured the projection would hand a reverted handler a pre-narrowed row and the leak would
vanish along with the bug. ``redis_manager`` is stubbed to a permanent miss so the catalogue
paths take their database branch rather than replaying a cached page.
"""

from __future__ import annotations

import ast
import copy
import inspect
import json
import os
import sys
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from fastapi.testclient import TestClient

from backend_app.backend.marketplace import listing_projection
from backend_app.core.dependencies import get_current_user
from backend_app.main import app
from backend_app.routers import library as library_router

client = TestClient(app, raise_server_exceptions=False)


# ══════════════════════════════════════════════════════════════════════════
# Identities and seeds
# ══════════════════════════════════════════════════════════════════════════

LISTING_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
SECOND_LISTING_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaab"
AUTHOR_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
AUTHOR_EMAIL = "creator.secret@private-identity.example"
CREATOR_ALIAS = "QuantWizard"

#: The caller: an authenticated NON-OWNER (a different id from AUTHOR_ID).
CALLER_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc"
CALLER_USER = {
    "id": CALLER_ID,
    "email": "viewer@test.example",
    "role": "authenticated",
    "app_metadata": {},
    "user_metadata": {},
}

#: The columns the task names as DENIED — every one must be absent from every response.
DENIED_COLUMNS = [
    "source_strategy_id",
    "moderation_notes",
    "moderated_by",
    "moderated_at",
    "evaluation_score",
    "deployment_requirements",
    "version_history",
    "equity_curve_snapshot",
    "risk_stop_loss_pct",
    "risk_take_profit_pct",
    "risk_max_position_size",
    "risk_max_drawdown_pct",
    "has_ml_model",
]

#: The identity values that must never appear anywhere in a serialised body (Requirement 6.5).
#: ``author_id`` is separated from the email because ONE endpoint legitimately echoes a
#: caller-supplied path segment — see ``_assert_no_identity``.
DENIED_IDENTITY_VALUES = [AUTHOR_ID, AUTHOR_EMAIL]


def _seeded_library_row(listing_id: str = LISTING_ID) -> Dict[str, Any]:
    """A PUBLISHED-visible ``library_strategies`` row that CARRIES a value in every denied
    column AND every column ``LISTING_SELECT`` requests — so the read succeeds and the
    absence assertions are non-vacuous."""
    return {
        # ── Columns LISTING_SELECT requests (project_listing must succeed) ──
        "id": listing_id,
        "name": "Momentum Breakout",
        "description": "A public strategy card.",
        "category": "momentum",
        "difficulty": "intermediate",
        "tags": ["trend", "breakout"],
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "exchange_id": "binance",
        "price": "9.99",
        "price_minor": 999,
        "currency": "USD",
        "subscriber_count": 42,
        "avg_rating": 4.5,
        "rating_count": 12,
        "published_at": "2026-07-01T00:00:00+00:00",
        "verification_status": "verified",
        "source_cloning_enabled": True,
        "supported_timeframes": ["1h", "4h"],
        "market_type": "spot",
        "condition_count": 2,
        "backtest_total_return_pct": "18.5",
        "backtest_sharpe_ratio": "1.7",
        "backtest_max_drawdown_pct": "9.0",
        "backtest_win_rate_pct": "56.0",
        "backtest_profit_factor": "1.9",
        "backtest_total_trades": 120,
        # ── The publish-time filter columns (PUBLISHED-visible) ──
        "is_active": True,
        "moderation_status": "approved",
        # `get_featured_strategies` filters on this one.
        "is_featured": True,
        "clone_count": 7,
        # ── DENIED columns, every one carrying a value (non-vacuity) ──
        "author_id": AUTHOR_ID,
        "author_email": AUTHOR_EMAIL,  # an author email on the row — must not leak
        "email": AUTHOR_EMAIL,
        "source_strategy_id": "44444444-4444-4444-4444-444444444444",
        "moderation_notes": "internal reviewer note - do not disclose",
        "moderated_by": "55555555-5555-5555-5555-555555555555",
        "moderated_at": "2026-06-30T12:00:00+00:00",
        "evaluation_score": 87.3,
        "deployment_requirements": {"min_capital": 1000, "leverage": 3},
        "version_history": [{"version": 1, "at": "2026-06-01"}],
        "equity_curve_snapshot": [100.0, 101.2, 99.8, 104.5],
        "risk_stop_loss_pct": 3.5,
        "risk_take_profit_pct": 7.0,
        "risk_max_position_size": 25000,
        "risk_max_drawdown_pct": 15.0,
        "has_ml_model": True,
    }


def _seeded_evidence_row(index: int) -> Dict[str, Any]:
    """One ``marketplace_backtest_evidence`` row for the detail read's condition summaries."""
    return {
        "condition_index": index,
        "total_return_pct": f"{10 + index}.5",
        "sharpe_ratio": f"1.{index}",
        "sortino_ratio": f"2.{index}",
        "max_drawdown_pct": f"{5 + index}.0",
        "win_rate_pct": f"{50 + index}.0",
        "profit_factor": f"1.{index}5",
        "total_trades": 100 + index,
        "marketplace_submissions": {
            "listing_id": LISTING_ID,
            "submission_state": "PUBLISHED",
        },
    }


# ══════════════════════════════════════════════════════════════════════════
# THE PRE-FIX CONSTRUCT, RECORDED MECHANICALLY (task 16.3 step 3)
# ══════════════════════════════════════════════════════════════════════════

#: The pre-fix construct, named part by part so a future reader — or Task 35.2's "each of the six
#: root-cause regression tests fails on a revert of its fix" check — can reproduce the revert
#: without rediscovering it. ``absent_from_source`` is what the source guard below asserts is
#: gone from ``get_library_detail``; ``present_in_source`` is what replaced it.
PRE_FIX_CONSTRUCT: Dict[str, Any] = {
    "handler": "backend_app.routers.library.get_library_detail",
    "spec_task": "marketplace-subscriptions-paper-trading / 16.1, 16.2",
    "requirements": ("6.4", "6.5"),
    "shape": '.select("*") + dict(resp.data) + pop("author_id")',
    "absent_from_source": {
        "star_select": 'a .select("*") call',
        "row_serialisation": "dict(<row>) as the response body",
        "enumerated_redaction": '.pop("author_id", …)',
    },
    "present_in_source": (
        "listing_projection.LISTING_SELECT",
        "listing_projection.project_listing",
    ),
    "revert_in_process": "tests…PRE_FIX_PROJECTION patched over library._listing_projection"
    ".project_listing",
}


def PRE_FIX_PROJECTION(
    row: Any,
    evidence_summaries: Optional[Sequence[Any]] = None,
    creator_alias: Optional[str] = None,
) -> Dict[str, Any]:
    """The pre-fix construct as a callable with ``project_listing``'s signature.

    ``dict(row)`` then ``pop("author_id")`` — the whole of the old shape, and nothing else.
    Patching this over ``library._listing_projection.project_listing`` reverts the fix on all
    eight public paths at once, because task 16.2 routed every one of them through the single
    serialiser. That is what makes the revert executable rather than a manual edit.
    """
    out = dict(row)
    out.pop("author_id", None)
    # The old shape's creator field, under its old name, so a revert fails on the LEAK rather
    # than on an unrelated missing key.
    out["author_alias"] = creator_alias
    return out


# ══════════════════════════════════════════════════════════════════════════
# A filter-aware fake Supabase double that IGNORES the select column list
# ══════════════════════════════════════════════════════════════════════════


class _Resp:
    def __init__(self, data: Any):
        self.data = data


class _Query:
    """A fluent builder that honours ``eq``/``in_``/``is_`` filters and ignores ``select``.

    Ignoring ``select`` is deliberate and load-bearing: it is what lets a reverted
    ``.select("*")`` handler receive the full row, so the leak the regression exists to catch is
    actually reachable. Filters ARE honoured, because the owner-scoped and publish-state-scoped
    reads differ only by their filter chain.

    ``single()`` unwraps a one-row match; a miss raises PostgREST's ``PGRST116``, which the
    handler classifies as a not-found rather than a read failure.
    """

    def __init__(self, table: str, db: "FakeSupabase"):
        self.table_name = table
        self.db = db
        self._eq: List[Tuple[str, Any]] = []
        self._in: List[Tuple[str, List[Any]]] = []
        self._is: List[Tuple[str, Any, bool]] = []
        self._single = False
        self._negate_next = False

    # ── chain ────────────────────────────────────────────────────────────
    def select(self, *a, **k):
        return self

    def eq(self, column, value):
        self._eq.append((column, value))
        return self

    def in_(self, column, values):
        self._in.append((column, list(values)))
        return self

    def gte(self, *a, **k):
        return self

    def contains(self, *a, **k):
        return self

    def or_(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def range(self, *a, **k):
        return self

    def single(self, *a, **k):
        self._single = True
        return self

    @property
    def not_(self):
        self._negate_next = True
        return self

    def is_(self, column, value):
        negated = self._negate_next
        self._negate_next = False
        self._is.append((column, value, negated))
        return self

    # ── evaluation ───────────────────────────────────────────────────────
    @staticmethod
    def _value(row: Dict[str, Any], column: str) -> Any:
        """``"a.b"`` reads the embedded resource ``a``'s column ``b``, as PostgREST does."""
        if "." in column:
            outer, inner = column.split(".", 1)
            embedded = row.get(outer) or {}
            return embedded.get(inner)
        return row.get(column)

    def _matches(self, row: Dict[str, Any]) -> bool:
        for column, value in self._eq:
            if self._value(row, column) != value:
                return False
        for column, values in self._in:
            if self._value(row, column) not in values:
                return False
        for column, value, negated in self._is:
            is_null = self._value(row, column) is None
            wants_null = value is None or str(value).lower() == "null"
            observed = is_null if wants_null else not is_null
            if observed == negated:
                return False
        return True

    def execute(self):
        self.db.calls.append((self.table_name, tuple(c for c, _ in self._eq)))
        rows = [
            copy.deepcopy(row)
            for row in self.db.tables.get(self.table_name, [])
            if self._matches(row)
        ]
        if self._single:
            if len(rows) != 1:
                raise _no_rows_error()
            return _Resp(rows[0])
        return _Resp(rows)


def _no_rows_error() -> Exception:
    """PostgREST's answer to a ``.single()`` that matched no row."""
    try:
        from postgrest.exceptions import APIError

        return APIError(
            {
                "code": "PGRST116",
                "message": "JSON object requested, multiple (or no) rows returned",
                "details": "Results contain 0 rows",
                "hint": None,
            }
        )
    except Exception:  # pragma: no cover - postgrest ships with supabase
        return RuntimeError("PGRST116")


class FakeSupabase:
    def __init__(self, tables: Dict[str, List[Dict[str, Any]]]):
        self.tables = tables
        self.calls: List[Tuple[str, Tuple[str, ...]]] = []

    def table(self, name: str):
        return _Query(name, self)


def _db() -> FakeSupabase:
    """The seeded database every path in this module reads.

    Two PUBLISHED-visible Listings by the one creator (``compare_strategies`` needs at least
    two ids), the creator's ``profiles`` alias row so ``resolve_aliases`` resolves rather than
    leaving the row unprojectable, one favourite marker owned by the CALLER so
    ``get_user_favorites`` has a non-empty page, an empty ``library_subscriptions`` so
    ``get_recommendations`` takes its trending branch, and the detail read's evidence rows.
    """
    return FakeSupabase(
        tables={
            "library_strategies": [
                _seeded_library_row(LISTING_ID),
                _seeded_library_row(SECOND_LISTING_ID),
            ],
            "profiles": [{"id": AUTHOR_ID, "display_name": CREATOR_ALIAS}],
            "library_ratings": [
                {
                    "library_id": LISTING_ID,
                    "user_id": CALLER_ID,
                    # `rating IS NULL` is how `favorite_strategy` marks a favourite.
                    "rating": None,
                    "review_text": None,
                    "is_verified_clone": False,
                    "created_at": "2026-07-02T00:00:00+00:00",
                }
            ],
            "library_subscriptions": [],
            "marketplace_backtest_evidence": [
                _seeded_evidence_row(1),
                _seeded_evidence_row(2),
            ],
        }
    )


class _NeverCached:
    """A ``redis_manager`` stub that always misses.

    The catalogue handlers serve an already-projected page from cache when one exists. A stub
    that always misses forces the database branch, so the assertions below are about the code
    under test and not about whatever a previous test left in a shared cache.
    """

    async def get(self, *a, **k):
        return None

    async def set(self, *a, **k):
        return True


# ══════════════════════════════════════════════════════════════════════════
# The eight public paths, and how to call each one
# ══════════════════════════════════════════════════════════════════════════

#: ``(name, method, path, json body, cards key, requires_auth, path-supplied identities)``.
#:
#: ``cards key`` is where the projected Listing cards live in the body; ``None`` means the body
#: IS the card (the detail read). ``requires_auth`` records whether an anonymous caller is
#: refused. ``path_identities`` records identifiers the CALLER supplied in the request path, and
#: is explained at ``_assert_no_identity``.
PUBLIC_PATHS: Tuple[Tuple[str, str, str, Optional[dict], Optional[str], bool, Tuple[str, ...]], ...] = (
    ("get_library_detail", "GET", f"/api/library/{LISTING_ID}", None, None, True, ()),
    ("browse_library", "GET", "/api/library", None, "items", False, ()),
    ("get_featured_strategies", "GET", "/api/library/featured", None, "items", False, ()),
    ("get_trending_strategies", "GET", "/api/library/trending", None, "items", False, ()),
    (
        "get_creator_profile",
        "GET",
        f"/api/library/creator/{AUTHOR_ID}",
        None,
        "strategies",
        False,
        (AUTHOR_ID,),
    ),
    (
        "compare_strategies",
        "POST",
        "/api/library/compare",
        {"library_ids": [LISTING_ID, SECOND_LISTING_ID]},
        "strategies",
        True,
        (),
    ),
    (
        "get_recommendations",
        "GET",
        "/api/library/recommendations",
        None,
        "recommendations",
        True,
        (),
    ),
    ("get_user_favorites", "GET", "/api/library/favorites", None, "favorites", True, ()),
)

PATH_BY_NAME = {row[0]: row for row in PUBLIC_PATHS}


def _call(
    name: str,
    *,
    authenticated: bool,
    projection: Optional[Callable[..., Dict[str, Any]]] = None,
):
    """Issue one request to the named path and return ``(response, FakeSupabase)``.

    ``authenticated=True`` installs the non-owner caller both as ``get_current_user`` (the
    routes that require auth) and as the bearer identity ``_user_id_from_credentials`` decodes
    (the routes whose auth is optional), so "authenticated non-owner" means the same thing on
    every path. ``authenticated=False`` sends no credentials at all.

    ``projection`` optionally replaces the one serialiser — that is the in-process revert.
    """
    _n, method, path, body, _cards, _auth, _ids = PATH_BY_NAME[name]
    db = _db()
    headers = {"Authorization": "Bearer test-token"} if authenticated else {}

    if authenticated:
        app.dependency_overrides[get_current_user] = lambda: CALLER_USER
    try:
        stack = [
            patch.object(library_router, "_build_service_client", return_value=db),
            patch.object(library_router, "redis_manager", _NeverCached()),
            patch.object(
                library_router,
                "decode_token_local",
                lambda token: {"sub": CALLER_ID} if authenticated else {},
            ),
        ]
        if projection is not None:
            stack.append(
                patch.object(
                    library_router._listing_projection, "project_listing", projection
                )
            )
        with _nested(stack):
            if method == "GET":
                resp = client.get(path, headers=headers)
            else:
                resp = client.post(path, json=body, headers=headers)
    finally:
        app.dependency_overrides.clear()
    return resp, db


class _nested:
    """Enter a list of context managers as one — ``ExitStack`` without the import dance."""

    def __init__(self, managers):
        self.managers = managers

    def __enter__(self):
        self.entered = []
        try:
            for manager in self.managers:
                manager.__enter__()
                self.entered.append(manager)
        except Exception:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *exc):
        for manager in reversed(self.entered):
            manager.__exit__(*exc)
        return False


# ══════════════════════════════════════════════════════════════════════════
# Recursive walk helpers and the shared assertions
# ══════════════════════════════════════════════════════════════════════════


def _all_keys(value: Any) -> List[str]:
    """Every mapping key appearing anywhere in ``value``, at any nesting depth."""
    keys: List[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            keys.append(str(key))
            keys.extend(_all_keys(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            keys.extend(_all_keys(item))
    return keys


def _cards(name: str, body: Any) -> List[Any]:
    """The projected Listing cards in ``body`` for the named path."""
    cards_key = PATH_BY_NAME[name][4]
    if cards_key is None:
        return [body]
    if not isinstance(body, dict):
        return []
    value = body.get(cards_key)
    return list(value) if isinstance(value, list) else []


def _assert_no_denied_columns(where: str, payload: Any) -> None:
    """No denied column, as a key, anywhere in ``payload`` (Requirement 6.4)."""
    leaked = sorted(set(_all_keys(payload)) & set(DENIED_COLUMNS))
    assert not leaked, (
        f"{where} leaked denied column(s) {leaked} — a DENIED_LISTING_COLUMNS value reached a "
        f"non-owner (Requirement 6.4). Payload: {json.dumps(payload, default=str)}"
    )


def _assert_no_identity(where: str, payload: Any, path_identities: Tuple[str, ...] = ()) -> None:
    """No ``author_id``, email or authentication identity anywhere in ``payload`` (Req 6.5).

    ``path_identities`` are identifiers the CALLER put in the request path. Only
    ``GET /api/library/creator/{creator_id}`` has one, and it echoes that segment back as
    ``creator_id``: an echo of the caller's own input discloses nothing the caller did not
    already hold, so it is excluded from the substring scan while the SAME value is still
    forbidden inside every projected card (the caller-supplied route is not a licence for the
    card to carry an owner identity). Emails are never excluded, on any path.
    """
    serialised = json.dumps(payload, default=str)
    for value in DENIED_IDENTITY_VALUES:
        if value in path_identities:
            continue
        assert value not in serialised, (
            f"identity value {value!r} appears in {where} — the creator must be a display "
            f"alias only, never an author_id, an email or an authentication identity "
            f"(Requirement 6.5). Payload: {serialised}"
        )
    assert "author_id" not in set(_all_keys(payload)), (
        f"{where} carries an 'author_id' key (Requirement 6.5). Payload: {serialised}"
    )
    assert "email" not in set(_all_keys(payload)), (
        f"{where} carries an 'email' key (Requirement 6.5). Payload: {serialised}"
    )


def _assert_creator_is_an_alias_only(where: str, card: Any) -> None:
    """The card represents its creator by the resolved display alias, and by nothing else."""
    assert isinstance(card, dict), f"{where}: expected a card mapping, got {type(card)}"
    assert card.get("creator_alias") == CREATOR_ALIAS, (
        f"{where}: the creator must be represented by the resolved display alias "
        f"{CREATOR_ALIAS!r}; got {card.get('creator_alias')!r} (Requirement 6.5)"
    )
    assert card["creator_alias"] != AUTHOR_ID
    assert card["creator_alias"] != AUTHOR_EMAIL


# ══════════════════════════════════════════════════════════════════════════
# 1. GET /api/library/{id} — the detail read (task 16.1)
# ══════════════════════════════════════════════════════════════════════════


def _get_detail():
    resp, _db_used = _call("get_library_detail", authenticated=True)
    return resp


def test_detail_returns_200_against_current_code():
    resp = _get_detail()
    assert resp.status_code == 200, resp.text


def test_detail_omits_every_denied_column_at_any_depth():
    """_Requirements: 6.4_"""
    resp = _get_detail()
    assert resp.status_code == 200, resp.text
    _assert_no_denied_columns("GET /api/library/{id}", resp.json())


def test_detail_never_exposes_author_id_or_email():
    """_Requirements: 6.5_"""
    resp = _get_detail()
    assert resp.status_code == 200, resp.text
    _assert_no_identity("GET /api/library/{id}", resp.json())


def test_detail_represents_creator_by_alias_only():
    """_Requirements: 6.5_"""
    resp = _get_detail()
    assert resp.status_code == 200, resp.text
    _assert_creator_is_an_alias_only("GET /api/library/{id}", resp.json())


def test_seed_is_non_vacuous():
    """The row genuinely carries every denied column and every LISTING_SELECT column, so the
    absence assertions are meaningful rather than trivially satisfied by a sparse row."""
    row = _seeded_library_row()
    for column in DENIED_COLUMNS:
        assert column in row and row[column] is not None, (
            f"seed must carry denied column {column!r} to be non-vacuous"
        )
    assert row["author_id"] == AUTHOR_ID
    assert row["email"] == AUTHOR_EMAIL
    for column in listing_projection.LISTING_SELECT.split(","):
        column = column.strip()
        assert column in row, f"seed missing LISTING_SELECT column {column!r}"


# ══════════════════════════════════════════════════════════════════════════
# 2. Every path task 16.2 repointed — authenticated non-owner AND anonymous
# ══════════════════════════════════════════════════════════════════════════

REPOINTED_PATHS = [row[0] for row in PUBLIC_PATHS if row[0] != "get_library_detail"]

#: The paths an anonymous caller can reach at all. The rest answer 401 before any read, and the
#: parametrised assertions below check that instead of a projected page.
ANONYMOUS_REACHABLE = {
    name for name, _m, _p, _b, _c, requires_auth, _i in PUBLIC_PATHS if not requires_auth
}


@pytest.mark.parametrize("name", REPOINTED_PATHS)
@pytest.mark.parametrize("authenticated", [True, False], ids=["non_owner", "anonymous"])
def test_repointed_path_omits_every_denied_column(name, authenticated):
    """A denied column reaching a catalogue card is the same defect as it reaching the detail
    body — for an authenticated non-owner and for an anonymous caller alike.

    _Requirements: 6.4_
    """
    resp, _db_used = _call(name, authenticated=authenticated)
    where = f"{name} (authenticated={authenticated})"

    if not authenticated and name not in ANONYMOUS_REACHABLE:
        assert resp.status_code == 401, (
            f"{where}: an anonymous caller must be refused, not served; got "
            f"{resp.status_code}: {resp.text}"
        )
    else:
        assert resp.status_code == 200, f"{where}: {resp.text}"

    _assert_no_denied_columns(where, resp.json())


@pytest.mark.parametrize("name", REPOINTED_PATHS)
@pytest.mark.parametrize("authenticated", [True, False], ids=["non_owner", "anonymous"])
def test_repointed_path_never_exposes_author_id_or_email(name, authenticated):
    """_Requirements: 6.5_"""
    resp, _db_used = _call(name, authenticated=authenticated)
    where = f"{name} (authenticated={authenticated})"
    path_identities = PATH_BY_NAME[name][6]

    body = resp.json()
    _assert_no_identity(where, body, path_identities)
    # The path-supplied echo is tolerated in the envelope, never inside a card.
    for index, card in enumerate(_cards(name, body)):
        _assert_no_identity(f"{where} card[{index}]", card)


@pytest.mark.parametrize("name", REPOINTED_PATHS)
@pytest.mark.parametrize("authenticated", [True, False], ids=["non_owner", "anonymous"])
def test_repointed_path_represents_creator_by_alias_only(name, authenticated):
    """Non-vacuity for the two assertions above: the reachable paths really do return cards.

    _Requirements: 6.5_
    """
    resp, _db_used = _call(name, authenticated=authenticated)
    where = f"{name} (authenticated={authenticated})"

    if not authenticated and name not in ANONYMOUS_REACHABLE:
        assert resp.status_code == 401, f"{where}: {resp.text}"
        assert not _cards(name, resp.json()), (
            f"{where}: a refused caller must be served no cards at all: {resp.text}"
        )
        return

    assert resp.status_code == 200, f"{where}: {resp.text}"
    cards = _cards(name, resp.json())
    assert cards, (
        f"{where} returned no cards; the leak assertions on this path would be vacuous. "
        f"Body: {resp.text}"
    )
    for index, card in enumerate(cards):
        _assert_creator_is_an_alias_only(f"{where} card[{index}]", card)


# ══════════════════════════════════════════════════════════════════════════
# 3. The revert-proof, executed mechanically (task 16.3 step 3)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("name", [row[0] for row in PUBLIC_PATHS])
def test_every_public_path_leaks_under_the_pre_fix_projection(name):
    """Patching :data:`PRE_FIX_PROJECTION` over the one serialiser reverts the fix in-process;
    every public path must then FAIL the very assertions above.

    This is the executable form of "a regression test that passes against the unfixed code is
    evidence of nothing". Task 35.2's "each root-cause regression test fails on a revert of its
    fix" check is this test: it holds the revert and the detection in one place, so nobody has
    to hand-edit ``library.py`` to confirm the guard bites.

    _Requirements: 6.4, 6.5_
    """
    resp, _db_used = _call(name, authenticated=True, projection=PRE_FIX_PROJECTION)
    assert resp.status_code == 200, (
        f"{name}: the reverted projection must still serve a body for the leak to be "
        f"observable; got {resp.status_code}: {resp.text}"
    )

    with pytest.raises(AssertionError) as denied:
        _assert_no_denied_columns(f"{name} (pre-fix revert)", resp.json())
    message = str(denied.value)
    for column in DENIED_COLUMNS:
        assert column in message, (
            f"{name}: the pre-fix revert leaked, but {column!r} was not named in the failure — "
            f"a regression that does not name the leaked column is not actionable. "
            f"Failure was: {message}"
        )

    with pytest.raises(AssertionError):
        _assert_no_identity(f"{name} (pre-fix revert)", resp.json())


def test_the_pre_fix_construct_is_absent_from_get_library_detail():
    """The source guard: the three parts of the pre-fix construct are gone from the handler.

    Reverting task 16.1 reintroduces at least one of them, so this fails on a revert without
    any database, fixture or request. :data:`PRE_FIX_CONSTRUCT` is the written record of what
    is being looked for.

    _Requirements: 6.4_
    """
    source = inspect.getsource(library_router.get_library_detail)
    tree = ast.parse(inspect.cleandoc(source.replace("@router.get", "# @router.get")))

    star_selects: List[int] = []
    row_dicts: List[int] = []
    author_id_pops: List[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "select":
            for arg in node.args:
                if isinstance(arg, ast.Constant) and arg.value == "*":
                    star_selects.append(node.lineno)
        if isinstance(func, ast.Attribute) and func.attr == "pop":
            if node.args and isinstance(node.args[0], ast.Constant):
                if node.args[0].value == "author_id":
                    author_id_pops.append(node.lineno)
        if isinstance(func, ast.Name) and func.id == "dict" and node.args:
            row_dicts.append(node.lineno)

    assert not star_selects, (
        "get_library_detail contains a .select(\"*\") at relative line(s) "
        f"{star_selects} — the pre-fix construct "
        f"{PRE_FIX_CONSTRUCT['absent_from_source']['star_select']} is back "
        f"(expected {PRE_FIX_CONSTRUCT['present_in_source'][0]})"
    )
    assert not row_dicts, (
        f"get_library_detail serialises a row with dict(...) at relative line(s) {row_dicts} — "
        f"the pre-fix construct {PRE_FIX_CONSTRUCT['absent_from_source']['row_serialisation']} "
        f"is back (expected {PRE_FIX_CONSTRUCT['present_in_source'][1]})"
    )
    assert not author_id_pops, (
        f"get_library_detail redacts by .pop(\"author_id\") at relative line(s) "
        f"{author_id_pops} — deny-by-enumeration cannot cover a column added by a later "
        "migration, which is the whole defect Requirement 6.4 names"
    )

    for expected in PRE_FIX_CONSTRUCT["present_in_source"]:
        symbol = expected.split(".")[-1]
        assert symbol in source, (
            f"get_library_detail no longer references {expected}; the allow-list projection is "
            "what replaced the pre-fix construct"
        )


def test_pre_fix_projection_reproduces_the_documented_construct():
    """:data:`PRE_FIX_PROJECTION` is the pre-fix shape and not an approximation of it.

    It must carry the denied columns straight through and drop exactly ``author_id`` — which is
    precisely why the old code passed review and still leaked: the one column somebody
    remembered was the one column that was covered.
    """
    row = _seeded_library_row()
    reverted = PRE_FIX_PROJECTION(row, None, CREATOR_ALIAS)

    assert "author_id" not in reverted, "the pre-fix construct did pop author_id"
    for column in DENIED_COLUMNS:
        assert reverted[column] == row[column], (
            f"the pre-fix construct passed {column!r} through unchanged; that is the leak"
        )
    assert reverted["email"] == AUTHOR_EMAIL
    # And the row it was handed is untouched, so a shared seed cannot be corrupted by a revert.
    assert row["author_id"] == AUTHOR_ID


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
