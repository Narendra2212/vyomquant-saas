"""PAPER-09 -- a trader can paper-trade a strategy of their own that was never published.

Production: ``POST /api/paper/sessions`` with the caller's own ``strategy_id`` answered 403
``LISTING_UNAVAILABLE`` while ``GET /api/library/my-strategies`` offered ``start_paper`` on that very
strategy. The admission decision was keyed on a Listing and an unpublished strategy has none.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from backend_app.backend.marketplace.entitlement_resolver import (
    EntitlementReason,
    resolve_owned_strategy,
)

ME = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
STRATEGY = "33333333-3333-4333-8333-333333333333"
VERSION = "44444444-4444-4444-8444-444444444444"


class _Q:
    def __init__(self, rows):
        self._rows = rows
        self._eq = {}

    def select(self, *_a, **_k):
        return self

    def eq(self, column, value):
        self._eq[column] = value
        return self

    def execute(self):
        rows = [r for r in self._rows if all(str(r.get(k)) == str(v) for k, v in self._eq.items())]
        return SimpleNamespace(data=rows, error=None)


class _DB:
    def __init__(self, strategies, versions):
        self._t = {"strategies": strategies, "strategy_versions": versions}

    def table(self, name):
        return _Q(self._t[name])


def _run(db, caller_id=ME, strategy_id=STRATEGY):
    return asyncio.run(resolve_owned_strategy({"id": caller_id}, strategy_id, db))


def _own(**over):
    row = {"id": STRATEGY, "user_id": ME, "archived_at": None}
    row.update(over)
    return row


_VERSIONS = [{"id": VERSION, "strategy_id": STRATEGY, "version": "v1.0", "is_draft": False}]


def test_an_owner_is_admitted_with_no_listing():
    e = _run(_DB([_own()], _VERSIONS))
    assert e.entitling is True and e.reason is EntitlementReason.OWNED
    assert e.listing_id is None
    assert e.source_strategy_id == STRATEGY and e.version_id == VERSION


def test_another_traders_strategy_is_refused_like_a_missing_one():
    other = _run(_DB([_own(user_id=OTHER)], _VERSIONS))
    missing = _run(_DB([], _VERSIONS))
    assert other.reason is EntitlementReason.LISTING_UNAVAILABLE
    assert missing.reason is EntitlementReason.LISTING_UNAVAILABLE
    assert other.wire_code == missing.wire_code, "the answer must not reveal which case it was"


def test_an_archived_strategy_is_refused():
    e = _run(_DB([_own(archived_at="2026-11-07T00:00:00Z")], _VERSIONS))
    assert e.entitling is False and e.reason is EntitlementReason.LISTING_UNAVAILABLE


def test_a_strategy_with_only_a_draft_version_is_refused():
    drafts = [{"id": VERSION, "strategy_id": STRATEGY, "version": "v1.0", "is_draft": True}]
    assert _run(_DB([_own()], drafts)).entitling is False


def test_a_caller_with_no_identity_is_refused():
    assert _run(_DB([_own()], _VERSIONS), caller_id=None).entitling is False


def test_the_session_start_consults_the_owned_path_only_after_the_listing_decision():
    import inspect

    from backend_app.backend.paper import paper_session_service as pss

    src = inspect.getsource(pss.start_session)
    assert src.index("resolve_entitlement(caller, listing_id") < src.index("resolve_owned_strategy(")
    assert "EntitlementReason.LISTING_UNAVAILABLE" in src
