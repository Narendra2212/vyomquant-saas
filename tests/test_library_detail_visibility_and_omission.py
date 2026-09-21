"""
tests/test_library_detail_visibility_and_omission.py

Task 16.1 of ``marketplace-subscriptions-paper-trading``: the four behaviours the rewritten
``GET /api/library/{library_id}`` owes beyond the allow-list projection itself (which
``tests/test_library_detail_projection_regression.py`` covers, and
``tests/test_listing_projection.py`` guards structurally).

WHAT IS ASSERTED
----------------
1. **Indistinguishability (Requirement 6.10).** A Listing whose Submission is not ``PUBLISHED``
   answers a non-owner with the SAME status and the SAME body as an identifier that names no row
   at all — the shared ``NOT_FOUND`` shape. Neither answer discloses the Listing's existence, its
   owner or its Submission_State.
2. **The owner still reads their own Listing.** The same non-published row that answers a
   non-owner ``NOT_FOUND`` is served, projected, to its author. The owner read is scoped by
   ``author_id`` in the query itself, so it can serve nobody else's row.
3. **``user_rating`` is OMITTED, not nulled (Requirement 28.5).** When the caller's own rating
   row cannot be read, neither ``user_rating`` nor ``user_has_cloned`` appears in the response.
   An omitted field and a ``null`` field mean different things: ``user_rating: null`` claims the
   caller rated this Listing and left the score blank.
4. **The per-condition summaries come from the immutable evidence copy (Requirements 6.3, 3.9).**
   ``condition_summaries`` carry ``label`` plus outcome metrics read from
   ``marketplace_backtest_evidence``, and carry no ``dataset``, window, ``dataset_checksum``,
   ``dag_hash``, ``engine_version``, ``executed_bar_count``, ``version_id`` or
   ``source_backtest_id``. A failed evidence read answers non-2xx rather than a Listing with an
   empty, fabricated condition set.

HARNESS
-------
FastAPI ``TestClient`` plus a FILTER-AWARE fake Supabase double. The filters matter here in a way
they do not for the projection test: the point of assertions 1 and 2 is precisely that one query
is gated on ``is_active``/``moderation_status`` and the other on ``author_id``, so a fake that
ignored the chain would make every assertion vacuous. ``.single()`` reproduces PostgREST's real
behaviour — ``PGRST116`` when the filtered read matches no row, which is what separates a miss
from a read failure.
"""

from __future__ import annotations

import copy
import json
import os
import sys
from typing import Any, Dict, List, Optional
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from fastapi.testclient import TestClient
from postgrest.exceptions import APIError

from backend_app.backend.marketplace.errors import NOT_FOUND
from backend_app.backend.marketplace.listing_projection import (
    CONDITION_OUTCOME_METRICS,
    DENIED_LISTING_COLUMNS,
)
from backend_app.core.dependencies import get_current_user
from backend_app.main import app

client = TestClient(app, raise_server_exceptions=False)


LISTING_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
ABSENT_LISTING_ID = "dddddddd-dddd-dddd-dddd-dddddddddddd"
AUTHOR_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
NON_OWNER_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc"
CREATOR_ALIAS = "QuantWizard"
SUBMISSION_ID = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"

#: Evidence columns that must never reach a public condition summary (Requirement 6.3).
EVIDENCE_ONLY_COLUMNS = (
    "dataset",
    "start_date",
    "end_date",
    "dataset_checksum",
    "dag_hash",
    "engine_version",
    "executed_bar_count",
    "version_id",
    "source_backtest_id",
    "initial_capital",
    "final_capital",
    "commission",
    "slippage",
)


def _user(user_id: str) -> Dict[str, Any]:
    return {
        "id": user_id,
        "email": f"{user_id}@test.example",
        "role": "authenticated",
        "app_metadata": {},
        "user_metadata": {},
    }


def _listing_row(*, published: bool) -> Dict[str, Any]:
    """A ``library_strategies`` row, published-visible or not.

    Non-published is expressed exactly as migration 007's projection expresses it:
    ``is_active = False`` and ``moderation_status = 'pending'`` — the pair every
    non-``PUBLISHED`` Submission_State projects to. The row carries a value in every denied
    column so an accidental 200 for a non-owner would be a visible leak, not a blank body.
    """
    return {
        "id": LISTING_ID,
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
        "condition_count": 3,
        "backtest_total_return_pct": "18.5",
        "backtest_sharpe_ratio": "1.7",
        "backtest_max_drawdown_pct": "9.0",
        "backtest_win_rate_pct": "56.0",
        "backtest_profit_factor": "1.9",
        "backtest_total_trades": 120,
        "is_active": published,
        "moderation_status": "approved" if published else "pending",
        "author_id": AUTHOR_ID,
        "source_strategy_id": "44444444-4444-4444-4444-444444444444",
        "moderation_notes": "internal reviewer note",
        "moderated_by": "55555555-5555-5555-5555-555555555555",
        "moderated_at": "2026-06-30T12:00:00+00:00",
        "evaluation_score": 87.3,
        "deployment_requirements": {"min_capital": 1000},
        "version_history": [{"version": 1}],
        "equity_curve_snapshot": [100.0, 101.2],
        "risk_stop_loss_pct": 3.5,
        "risk_take_profit_pct": 7.0,
        "risk_max_position_size": 25000,
        "risk_max_drawdown_pct": 15.0,
        "has_ml_model": True,
    }


def _evidence_row(index: int) -> Dict[str, Any]:
    """One ``marketplace_backtest_evidence`` row, carrying the excluded columns too.

    The embedded ``marketplace_submissions`` resource is what the handler filters on; it is
    present here so the filter-aware fake can evaluate ``marketplace_submissions.listing_id``
    and ``…submission_state`` the way PostgREST would.
    """
    return {
        "condition_index": index,
        "total_return_pct": f"{10 + index}.5",
        "sharpe_ratio": f"1.{index}",
        "sortino_ratio": f"2.{index}",
        "max_drawdown_pct": f"{5 + index}.0",
        "win_rate_pct": f"{50 + index}.0",
        "profit_factor": f"1.{index}5",
        "total_trades": 100 + index,
        # Everything below must never reach the response.
        "dataset": f"BTCUSDT-1h-secret-dataset-{index}",
        "start_date": "2025-01-01",
        "end_date": "2025-06-30",
        "dataset_checksum": f"checksum-{index}-do-not-disclose",
        "dag_hash": f"daghash-{index}-do-not-disclose",
        "engine_version": "backtest-engine/2.1.0",
        "executed_bar_count": 4321,
        "version_id": "66666666-6666-6666-6666-666666666666",
        "source_backtest_id": "77777777-7777-7777-7777-777777777777",
        "initial_capital": "10000",
        "final_capital": "11850",
        "commission": "0.001",
        "slippage": "0.0005",
        "marketplace_submissions": {
            "listing_id": LISTING_ID,
            "submission_state": "PUBLISHED",
        },
    }


# ══════════════════════════════════════════════════════════════════════════
# A filter-aware fake Supabase double
# ══════════════════════════════════════════════════════════════════════════


def _no_rows_error() -> APIError:
    """PostgREST's answer to a ``.single()`` that matched no row."""
    return APIError(
        {
            "code": "PGRST116",
            "message": "JSON object requested, multiple (or no) rows returned",
            "details": "Results contain 0 rows",
            "hint": None,
        }
    )


def _read_failed_error() -> APIError:
    """A read that did not complete — anything but a ``.single()`` miss."""
    return APIError(
        {
            "code": "57014",
            "message": "canceling statement due to statement timeout",
            "details": "",
            "hint": None,
        }
    )


class _Resp:
    def __init__(self, data: Any):
        self.data = data


class _Query:
    """A fluent builder that RESPECTS ``eq``/``in_`` filters, including embedded ones."""

    def __init__(self, table: str, db: "FakeSupabase"):
        self.table_name = table
        self.db = db
        self._eq: List[tuple] = []
        self._in: List[tuple] = []
        self._single = False

    # ── chain ────────────────────────────────────────────────────────────
    def select(self, *a, **k):
        return self

    def eq(self, column, value):
        self._eq.append((column, value))
        return self

    def in_(self, column, values):
        self._in.append((column, list(values)))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def single(self, *a, **k):
        self._single = True
        return self

    @property
    def not_(self):
        return self

    def is_(self, *a, **k):
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
        return True

    def execute(self):
        self.db.calls.append(self.table_name)
        failure = self.db.failures.get(self.table_name)
        if failure is not None:
            raise failure
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


class FakeSupabase:
    def __init__(
        self,
        tables: Dict[str, List[Dict[str, Any]]],
        failures: Optional[Dict[str, Exception]] = None,
    ):
        self.tables = tables
        self.failures = failures or {}
        self.calls: List[str] = []

    def table(self, name: str):
        return _Query(name, self)


def _db(
    *,
    published: bool = True,
    listing_present: bool = True,
    evidence_conditions: int = 2,
    ratings: Optional[List[Dict[str, Any]]] = None,
    failures: Optional[Dict[str, Exception]] = None,
) -> FakeSupabase:
    listings = [_listing_row(published=published)] if listing_present else []
    evidence = [_evidence_row(i) for i in range(1, evidence_conditions + 1)]
    return FakeSupabase(
        tables={
            "library_strategies": listings,
            "marketplace_backtest_evidence": evidence,
            "profiles": [{"id": AUTHOR_ID, "display_name": CREATOR_ALIAS}],
            "library_ratings": ratings if ratings is not None else [],
        },
        failures=failures,
    )


def _get(listing_id: str, caller_id: str, db: FakeSupabase):
    app.dependency_overrides[get_current_user] = lambda: _user(caller_id)
    try:
        with patch(
            "backend_app.routers.library._build_service_client", return_value=db
        ):
            return client.get(f"/api/library/{listing_id}")
    finally:
        app.dependency_overrides.clear()


def _all_keys(value: Any) -> List[str]:
    keys: List[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            keys.append(str(key))
            keys.extend(_all_keys(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            keys.extend(_all_keys(item))
    return keys


# ══════════════════════════════════════════════════════════════════════════
# 1. Indistinguishability (Requirement 6.10)
# ══════════════════════════════════════════════════════════════════════════


def test_non_published_listing_answers_a_non_owner_exactly_as_an_absent_id():
    """Same status, same body: a hidden Listing and an id that names no row.

    _Requirements: 6.10_
    """
    hidden = _get(LISTING_ID, NON_OWNER_ID, _db(published=False))
    absent = _get(ABSENT_LISTING_ID, NON_OWNER_ID, _db(listing_present=False))

    assert hidden.status_code == 404, hidden.text
    assert absent.status_code == hidden.status_code, (
        f"a hidden Listing answers {hidden.status_code} and an absent id "
        f"{absent.status_code}; the two must be indistinguishable"
    )
    # `request_id` is per-request by construction; everything that describes the outcome must
    # match exactly.
    assert hidden.json()["error"] == absent.json()["error"], (
        f"hidden={hidden.text} absent={absent.text}"
    )
    assert hidden.json()["error"]["code"] == NOT_FOUND


def test_the_not_found_body_discloses_nothing_about_the_hidden_listing():
    """No Listing_Projection field, no owner identity, no Submission_State in the body.

    _Requirements: 6.10_
    """
    hidden = _get(LISTING_ID, NON_OWNER_ID, _db(published=False))
    assert hidden.status_code == 404, hidden.text
    serialised = json.dumps(hidden.json()).lower()

    for leaked in (AUTHOR_ID.lower(), "momentum breakout", "pending", "unpublished",
                   "suspended", "moderation", CREATOR_ALIAS.lower()):
        assert leaked not in serialised, (
            f"the not-found body discloses {leaked!r}: {hidden.text}"
        )
    # And none of the projection's own fields is present.
    assert "listing_id" not in hidden.json()
    assert "price_minor" not in hidden.json()


# ══════════════════════════════════════════════════════════════════════════
# 2. The owner still reads their own non-published Listing
# ══════════════════════════════════════════════════════════════════════════


def test_owner_reads_their_own_non_published_listing():
    """The author is served the row a non-owner cannot see — projected, not serialised.

    _Requirements: 6.1, 6.4, 6.10_
    """
    owner_view = _get(LISTING_ID, AUTHOR_ID, _db(published=False))
    assert owner_view.status_code == 200, owner_view.text

    body = owner_view.json()
    assert body["listing_id"] == LISTING_ID
    assert body["creator_alias"] == CREATOR_ALIAS
    # The owner's view is the same allow-listed projection: no denied column, even here.
    leaked = sorted(set(_all_keys(body)) & set(DENIED_LISTING_COLUMNS))
    assert not leaked, f"owner view leaked denied column(s) {leaked}: {owner_view.text}"
    # No evidence read is attributed to a non-published Listing, so the condition count is the
    # row's stored figure rather than a 0 nothing measured (Requirement 28.5).
    assert body["condition_count"] == 3
    assert body["condition_summaries"] == []


def test_a_non_owner_is_not_served_by_the_owner_scoped_read():
    """The owner fallback is scoped by ``author_id``; it serves no one else's row.

    _Requirements: 6.10_
    """
    other = _get(LISTING_ID, NON_OWNER_ID, _db(published=False))
    assert other.status_code == 404, other.text


# ══════════════════════════════════════════════════════════════════════════
# 3. `user_rating` is omitted, never nulled (Requirement 28.5)
# ══════════════════════════════════════════════════════════════════════════


def test_user_rating_is_omitted_when_the_caller_rating_read_fails():
    """A failed enrichment read omits both fields rather than reporting ``null``.

    _Requirements: 28.5_
    """
    db = _db(failures={"library_ratings": _read_failed_error()})
    resp = _get(LISTING_ID, NON_OWNER_ID, db)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "user_rating" not in body, (
        f"user_rating is present ({body.get('user_rating')!r}) after a failed read; an "
        f"omitted field and a null field mean different things (Requirement 28.5)"
    )
    assert "user_has_cloned" not in body, resp.text


def test_user_rating_is_omitted_when_the_caller_has_no_rating_row():
    """No rating row is not a failure — and still not a ``null`` score.

    _Requirements: 28.5_
    """
    resp = _get(LISTING_ID, NON_OWNER_ID, _db(ratings=[]))
    assert resp.status_code == 200, resp.text
    assert "user_rating" not in resp.json(), resp.text


def test_user_rating_is_present_when_the_caller_has_rated():
    """Non-vacuity for the two omission tests: the field DOES appear when it was read."""
    ratings = [
        {
            "library_id": LISTING_ID,
            "user_id": NON_OWNER_ID,
            "rating": 4,
            "review_text": "solid",
            "created_at": "2026-07-02T00:00:00+00:00",
            "is_verified_clone": True,
        }
    ]
    resp = _get(LISTING_ID, NON_OWNER_ID, _db(ratings=ratings))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user_rating"] == 4
    assert body["user_has_cloned"] is True


# ══════════════════════════════════════════════════════════════════════════
# 4. Condition summaries come from the immutable evidence copy
# ══════════════════════════════════════════════════════════════════════════


def test_condition_summaries_are_read_from_the_immutable_evidence_copy():
    """Labels plus outcome metrics, sourced from ``marketplace_backtest_evidence``.

    _Requirements: 6.3, 3.9_
    """
    db = _db(evidence_conditions=2)
    resp = _get(LISTING_ID, NON_OWNER_ID, db)

    assert resp.status_code == 200, resp.text
    assert "marketplace_backtest_evidence" in db.calls, (
        "the detail read never touched marketplace_backtest_evidence; the per-condition "
        "summaries would not be the immutable copy"
    )

    body = resp.json()
    summaries = body["condition_summaries"]
    assert [s["label"] for s in summaries] == ["Condition 1", "Condition 2"]
    assert body["condition_count"] == 2

    first = _evidence_row(1)
    for metric in CONDITION_OUTCOME_METRICS:
        assert summaries[0][metric] == first[metric], (
            f"condition metric {metric} is {summaries[0][metric]!r}, not the evidence copy's "
            f"{first[metric]!r}"
        )
    # Each summary carries the label and the metrics — and nothing else.
    assert set(summaries[0]) == {"label", *CONDITION_OUTCOME_METRICS}


def test_condition_summaries_carry_no_dataset_window_or_fingerprint():
    """No dataset, window, checksum, graph hash or identifier at any depth.

    _Requirements: 6.3_
    """
    resp = _get(LISTING_ID, NON_OWNER_ID, _db(evidence_conditions=2))
    assert resp.status_code == 200, resp.text

    keys = set(_all_keys(resp.json()))
    leaked = sorted(keys & set(EVIDENCE_ONLY_COLUMNS))
    assert not leaked, f"condition summaries leaked {leaked}: {resp.text}"

    serialised = json.dumps(resp.json())
    for value in ("secret-dataset", "do-not-disclose", "backtest-engine/2.1.0"):
        assert value not in serialised, (
            f"evidence value {value!r} appears in the detail response: {serialised}"
        )
    # The embedded submission the filter rode on is not response content either.
    assert "submission_state" not in keys
    assert "marketplace_submissions" not in keys


def test_a_failed_evidence_read_is_not_answered_with_an_empty_condition_set():
    """A read that did not complete never becomes a Listing with zero conditions.

    _Requirements: 1.5, 28.5_
    """
    db = _db(failures={"marketplace_backtest_evidence": _read_failed_error()})
    resp = _get(LISTING_ID, NON_OWNER_ID, db)

    assert resp.status_code >= 500, (
        f"a failed evidence read answered {resp.status_code}: {resp.text}"
    )
    assert "condition_summaries" not in resp.text


def test_a_failed_listing_read_is_not_answered_as_not_found():
    """A read failure and a miss are different answers (Requirements 1.5, 1.7)."""
    db = _db(failures={"library_strategies": _read_failed_error()})
    resp = _get(LISTING_ID, NON_OWNER_ID, db)

    assert resp.status_code >= 500, resp.text
    assert resp.json()["error"]["code"] != NOT_FOUND


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
