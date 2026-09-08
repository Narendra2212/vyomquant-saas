"""
tests/test_marketplace_pipeline.py

End-to-end pipeline test for the Strategy Library / Marketplace.

WHAT IS TESTED
--------------
Full publish → admin-approves → browse → clone → rate pipeline:

1. P0-1 regression confirmation: get_admin_user correctly admits admins and
   rejects non-admins (direct unit test, does not depend on DB).
2. publish_strategy creates a library entry with moderation_status="pending".
3. admin_pending_strategies returns the newly published strategy before approval.
4. admin_moderate_strategy approves the strategy (status → "approved").
5. admin_pending_strategies returns an empty list after approval.
6. browse_library returns the approved strategy (and excludes pending ones).
7. clone_strategy (user-2) succeeds for an approved strategy.
8. self-clone prevention: user-1 (author) cannot clone their own strategy (409).
9. rate_strategy (user-2) succeeds after cloning (verified_clone marker set).
10. Pending/rejected strategies are not available for cloning (404).
11. unpublish_strategy removes the strategy from the browsable/cloneable pool.

DESIGN NOTES
------------
- All Supabase I/O is intercepted via `unittest.mock.patch` on
  `backend_app.routers.library._build_service_client`.
- Authentication is bypassed using FastAPI's `app.dependency_overrides`:
  `get_current_user` is replaced per-test with a lambda that returns the
  appropriate user dict directly, exactly as test_admin_auth.py does.
  This is the correct pattern for this codebase — raw JWT tokens cannot
  pass auth in the test environment (no live Supabase connection for
  profile/freeze verification).
- A lightweight `FakeDB` class provides in-process table storage with just
  enough filtering logic to exercise every branch of the production code.
- The admin user dict uses app_metadata.role="admin" matching the P0-1 fix
  in get_admin_user (checks app_metadata.role first, then user_metadata.role).
"""

import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from unittest.mock import patch

import pytest

# ─── Path and environment bootstrapping ───────────────────────────────────────
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient

from backend_app.core.dependencies import get_admin_user, get_current_user, get_request_supabase
from backend_app.core.subscription_dependencies import (
    require_marketplace_publish,
    check_marketplace_publish_quota,
    require_marketplace_access,
)
from backend_app.main import app

client = TestClient(app, raise_server_exceptions=True)


# ─── Shared user identities ───────────────────────────────────────────────────

AUTHOR_ID = str(uuid.uuid4())
CLONER_ID = str(uuid.uuid4())
ADMIN_ID = str(uuid.uuid4())

AUTHOR_USER = {
    "id": AUTHOR_ID,
    "email": "author@test.vyomquant.io",
    "role": "authenticated",
    "app_metadata": {"role": "authenticated"},
    "user_metadata": {},
}
CLONER_USER = {
    "id": CLONER_ID,
    "email": "cloner@test.vyomquant.io",
    "role": "authenticated",
    "app_metadata": {"role": "authenticated"},
    "user_metadata": {},
}
ADMIN_USER = {
    "id": ADMIN_ID,
    "email": "admin@test.vyomquant.io",
    "role": "authenticated",
    "app_metadata": {"role": "admin"},  # ← this is what the P0-1 fix reads
    "user_metadata": {},
}


def with_user(user_dict: dict):
    """Override get_current_user and subscription gates to return the given user dict."""
    app.dependency_overrides[get_current_user] = lambda: user_dict
    app.dependency_overrides[get_request_supabase] = lambda: None
    app.dependency_overrides[require_marketplace_publish] = lambda: True
    app.dependency_overrides[check_marketplace_publish_quota] = lambda: True
    app.dependency_overrides[require_marketplace_access] = lambda: True


def clear_overrides():
    app.dependency_overrides.clear()


# ─── Shared strategy IDs ──────────────────────────────────────────────────────

STRATEGY_UUID = str(uuid.uuid4())
# The single saved, immutable Strategy_Version every eligible Backtest_Evidence row references
# (Requirement 3.3 — one version across the whole evidence set, checked by EV_ONE_VERSION).
VERSION_UUID = str(uuid.uuid4())


# ─── In-memory fake database ──────────────────────────────────────────────────

class FakeTable:
    """
    Minimal, chain-able fake of a Supabase table handle.

    Supports: select / eq / in_ / single / insert / update / upsert / execute
    Filtering is applied at execute() time based on the accumulated chain.
    """

    def __init__(self, rows: list):
        self._rows = rows          # shared mutable reference (same object as FakeDB)
        self._filters: list = []
        self._op: Optional[str] = None
        self._op_data: Any = None
        self._single = False
        self._upsert_conflict: Optional[str] = None

    def select(self, *args, **kwargs) -> "FakeTable":
        return self

    def eq(self, col: str, val: Any) -> "FakeTable":
        self._filters.append(("eq", col, val))
        return self

    def in_(self, col: str, vals: list) -> "FakeTable":
        self._filters.append(("in", col, vals))
        return self

    def order(self, *args, **kwargs) -> "FakeTable":
        return self

    def limit(self, *args, **kwargs) -> "FakeTable":
        return self

    def not_(self) -> "FakeTable":
        return self

    def is_(self, *args) -> "FakeTable":
        return self

    def contains(self, *args, **kwargs) -> "FakeTable":
        return self

    def or_(self, *args, **kwargs) -> "FakeTable":
        return self

    def gte(self, *args, **kwargs) -> "FakeTable":
        return self

    def single(self) -> "FakeTable":
        self._single = True
        return self

    def insert(self, data: Any) -> "FakeTable":
        self._op = "insert"
        self._op_data = data
        return self

    def update(self, data: dict) -> "FakeTable":
        self._op = "update"
        self._op_data = data
        return self

    def upsert(self, data: dict, **kwargs) -> "FakeTable":
        self._op = "upsert"
        self._op_data = data
        conflict = kwargs.get("on_conflict", "")
        self._upsert_conflict = conflict
        return self

    def delete(self) -> "FakeTable":
        self._op = "delete"
        return self

    def execute(self):
        # --- INSERT ---
        if self._op == "insert":
            if isinstance(self._op_data, dict):
                if "id" not in self._op_data:
                    self._op_data["id"] = str(uuid.uuid4())
                self._rows.append(self._op_data)
                result = [self._op_data]
            else:
                for row in self._op_data:
                    if "id" not in row:
                        row["id"] = str(uuid.uuid4())
                    self._rows.append(row)
                result = self._op_data
            return _Resp(result[0] if self._single else result)

        # --- UPSERT ---
        if self._op == "upsert":
            conflict_cols = [c.strip() for c in (self._upsert_conflict or "").split(",") if c.strip()]
            matched = None
            if conflict_cols:
                for row in self._rows:
                    if all(str(row.get(c)) == str(self._op_data.get(c)) for c in conflict_cols):
                        matched = row
                        break
            if matched:
                matched.update(self._op_data)
                result = [matched]
            else:
                if "id" not in self._op_data:
                    self._op_data["id"] = str(uuid.uuid4())
                self._rows.append(self._op_data)
                result = [self._op_data]
            return _Resp(result[0] if self._single else result)

        # --- Materialise filter predicates ---
        result = list(self._rows)
        for ftype, *args in self._filters:
            if ftype == "eq":
                col, val = args
                result = [r for r in result if str(r.get(col, "")) == str(val)]
            elif ftype == "in":
                col, vals = args
                str_vals = [str(v) for v in vals]
                result = [r for r in result if str(r.get(col, "")) in str_vals]

        # --- UPDATE ---
        if self._op == "update":
            ids = {r["id"] for r in result}
            for row in self._rows:
                if row.get("id") in ids:
                    row.update(self._op_data)
            return _Resp(result)

        # --- DELETE ---
        if self._op == "delete":
            ids = {id(r) for r in result}
            survivors = [r for r in self._rows if id(r) not in ids]
            self._rows[:] = survivors
            return _Resp(result)

        # --- SELECT ---
        if self._single:
            if not result:
                # Supabase returns data: None when single() finds no rows
                return _Resp(None)
            return _Resp(result[0])
        return _Resp(result)


class _Resp:
    def __init__(self, data):
        self.data = data


class FakeDB:
    """Holds all table stores and returns FakeTable wrappers."""

    def __init__(self):
        self.stores: Dict[str, list] = {
            "strategies": [],
            "library_strategies": [],
            "library_ratings": [],
            "profiles": [],
            # Task 14.1/14.4 submission flow: the Eligibility_Gate's four reads and the
            # submission_service's writes land in these tables.
            "strategy_versions": [],
            "strategy_backtests": [],
            "marketplace_submissions": [],
            "marketplace_backtest_evidence": [],
            "marketplace_submission_transitions": [],
        }

    def table(self, name: str) -> FakeTable:
        # Any table the production code touches must resolve to a store; a KeyError here
        # would be a missing seed rather than a genuine test outcome.
        if name not in self.stores:
            self.stores[name] = []
        return FakeTable(self.stores[name])


# ─── Row builder helpers ──────────────────────────────────────────────────────

def _pending_lib_row(lib_id: str, author_id: str, source_id: str) -> dict:
    return {
        "id": lib_id,
        "author_id": author_id,
        "source_strategy_id": source_id,
        "name": "Test Strategy",
        "category": "momentum",
        "difficulty": "intermediate",
        "tags": ["btc"],
        "symbol": "BTC/USD",
        "timeframe": "1h",
        "exchange_id": "binance",
        "node_count": 1,
        "has_ml_model": False,
        "clone_count": 0,
        "rating_count": 0,
        "avg_rating": None,
        "moderation_status": "pending",
        "is_active": True,
        "is_featured": False,
        "published_at": "2026-07-01T00:00:00+00:00",
        "updated_at": "2026-07-01T00:00:00+00:00",
        "backtest_total_return_pct": 42.5,
        "backtest_sharpe_ratio": 1.8,
        "backtest_max_drawdown_pct": -5.2,
        "backtest_win_rate_pct": 61.0,
        "backtest_total_trades": 120,
        "equity_curve_snapshot": None,
    }


def _approved_lib_row(lib_id: str, author_id: str, source_id: str) -> dict:
    row = _pending_lib_row(lib_id, author_id, source_id)
    row["moderation_status"] = "approved"
    row["moderated_by"] = ADMIN_ID
    return row


def _entitling_subscription_row(subscriber_id: str) -> dict:
    """One `library_subscriptions` row that entitles ``subscriber_id`` right now (task 17.1).

    The Entitlement_Resolver's conditions for reason ``SUBSCRIBED``, and only those: the row is
    the caller's own (``user_id``), its persisted status is the lowercase ``'active'``
    ``library_subscriptions.status`` text, and its ``period_expiry`` is in the future. The expiry
    check is sweep-independent (Requirement 11.7), so a future instant is what makes it entitling
    — the ``status`` label alone is never enough.
    """
    return {
        "id": str(uuid.uuid4()),
        "user_id": subscriber_id,
        "status": "active",
        "period_expiry": (
            datetime.now(timezone.utc) + timedelta(days=30)
        ).isoformat(),
    }


def _cloneable_lib_row(
    lib_id: str,
    author_id: str,
    source_id: str,
    *,
    subscriber_id: Optional[str] = None,
    source_cloning_enabled: bool = True,
    submission_state: str = "PUBLISHED",
) -> dict:
    """An approved listing row as **both** readers of it see it during a clone (task 17.2).

    ``clone_strategy`` reads the flat columns (``is_active``, ``moderation_status``,
    ``clone_count``, ``author_id``, ``source_strategy_id``, ``name``,
    ``source_cloning_enabled``); the Entitlement_Resolver reads ``author_id``,
    ``source_strategy_id`` and the two **embedded** collections its one round trip selects
    (``marketplace_submissions(submission_state)`` and
    ``library_subscriptions(id,user_id,status,period_expiry)``). ``FakeTable.select`` is a no-op,
    so the embeds live on the row exactly as PostgREST returns them — the same shape
    ``tests/property/test_protected_logic_containment.py`` builds for P-48.

    ``source_cloning_enabled=False`` reproduces the **default** state: migration 007 adds the
    column defaulted ``FALSE``, so a listing whose owner has never called
    ``PATCH /api/library/{id}/settings`` is not cloneable (Requirements 7.3, 7.4).
    """
    row = _approved_lib_row(lib_id, author_id, source_id)
    row["source_cloning_enabled"] = source_cloning_enabled
    row["marketplace_submissions"] = [{"submission_state": submission_state}]
    row["library_subscriptions"] = (
        [] if subscriber_id is None else [_entitling_subscription_row(subscriber_id)]
    )
    return row


def _open_submission_row(
    source_id: str,
    owner_id: str,
    state: str = "SUBMITTED",
    *,
    submission_id: Optional[str] = None,
) -> dict:
    """One ``marketplace_submissions`` row for a listing's ``source_strategy_id`` (task 14.4/14.5).

    This is the row ``admin_moderate_strategy``'s narrowing guard
    (``_current_submission_state_for_listing``) reads to decide whether the retained PATCH route
    may accept a ``moderation_status`` (task 14.5), and it is also the row the six admin
    submission actions of task 14.4 transition. It carries the minimum columns those two paths
    read: the identity/ownership columns, the authoritative ``submission_state`` and a
    ``created_at`` for the "most recent" tie-break in the guard.
    """
    return {
        "id": submission_id or str(uuid.uuid4()),
        "source_strategy_id": source_id,
        "owner_id": owner_id,
        "version_id": VERSION_UUID,
        "listing_id": None,
        "submission_state": state,
        "rejection_reason": None,
        "reviewed_by": None,
        "reviewed_at": None,
        "submitted_at": "2026-07-01T00:00:00+00:00",
        "published_at": None,
        "created_at": "2026-07-01T00:00:00+00:00",
        "updated_at": "2026-07-01T00:00:00+00:00",
    }


# ─── Submission-flow row builders (task 14.1/14.4 Eligibility_Gate) ────────────
#
# These build the rows the Eligibility_Gate reads in its four owner-scoped round trips so that
# every criterion of Requirements 2 and 3 passes and the strategy is *admitted*. The gate
# derives nothing from ``strategies.backtest_result`` (task 14.10); admission rests entirely on
# the three ``strategy_backtests`` rows referenced by ``backtest_ids`` plus the saved version.


def _saved_version_row(strategy_id: str, version_id: str = VERSION_UUID) -> dict:
    """One saved, non-draft, VALID Strategy_Version — satisfies MP_VERSION_EXISTS/VALID (Req 2.3)."""
    return {
        "id": version_id,
        "strategy_id": strategy_id,
        "version": 1,
        "is_draft": False,
        # A persisted VALID verdict is the cheapest way _version_graph_is_valid admits a
        # version (it never has to load the graph), matching Requirement 2.3's reuse of the
        # Strategy_Builder's own structural validation.
        "validation_state": "VALID",
        "blueprint": {"nodes": [{"type": "action", "label": "BUY"}]},
        "graph_json": {"nodes": [{"type": "action", "label": "BUY"}]},
    }


def _eligible_backtest_row(
    backtest_id: str,
    owner_id: str,
    strategy_id: str,
    *,
    start_date: str,
    end_date: str,
    dataset: str,
    dataset_checksum: str,
    version_id: str = VERSION_UUID,
) -> dict:
    """One completed ``strategy_backtests`` row that passes every EV_* and MP_* criterion.

    Windows are >= 90 inclusive days (EV_DURATION), trades >= 20 (EV_TRADES), bars >= 50
    (EV_BARS), every Requirement 3.4 parameter is recorded and numeric (EV_PARAMS), all seven
    Requirement 2.6 metrics are present and finite (MP_METRICS_COMPLETE), and each row carries a
    different ``dataset``/``dataset_checksum`` so every pair is distinct (EV_DISTINCT/EV_CHECKSUMS).
    """
    return {
        "id": backtest_id,
        "user_id": owner_id,
        "strategy_id": strategy_id,
        "version_id": version_id,
        "status": "completed",
        "completed_at": "2026-06-01T00:00:00+00:00",
        "error_message": None,
        "dataset": dataset,
        "start_date": start_date,
        "end_date": end_date,
        "initial_capital": 10000,
        "commission": 0.001,
        "slippage": 0.0005,
        "dataset_checksum": dataset_checksum,
        "dag_hash": "dag-" + backtest_id[:8],
        "total_trades": 120,
        "executed_bar_count": 500,
        # The seven display metrics of Requirement 2.6 (MP_METRICS_COMPLETE reads these):
        "total_return_pct": 42.5,
        "sharpe_ratio": 1.8,
        "max_drawdown": -5.2,
        "win_rate": 61.0,
        "profit_factor": 1.7,
        "final_capital": 14250.0,
    }


def _three_eligible_backtests(owner_id: str, strategy_id: str) -> list:
    """Three eligible, pairwise-distinct Backtest_Evidence rows (Requirement 3.1's minimum)."""
    return [
        _eligible_backtest_row(
            str(uuid.uuid4()), owner_id, strategy_id,
            start_date="2024-01-01", end_date="2024-06-30",
            dataset="BTCUSD-2024H1", dataset_checksum="chk-btc-h1",
        ),
        _eligible_backtest_row(
            str(uuid.uuid4()), owner_id, strategy_id,
            start_date="2024-07-01", end_date="2024-12-31",
            dataset="ETHUSD-2024H2", dataset_checksum="chk-eth-h2",
        ),
        _eligible_backtest_row(
            str(uuid.uuid4()), owner_id, strategy_id,
            start_date="2023-01-01", end_date="2023-06-30",
            dataset="SOLUSD-2023H1", dataset_checksum="chk-sol-h1",
        ),
    ]


def _seed_eligible_submission(db, owner_id: str, strategy_id: str) -> list:
    """Seed a strategy's saved version and three eligible backtests; return their ids.

    Leaves ``marketplace_submissions`` and ``library_strategies`` empty for this strategy, so
    MP_SUBMISSION_OPEN passes (no open Submission, no PUBLISHED Listing) and the Eligibility_Gate
    admits. The returned ids are exactly what a ``POST /api/library/submissions`` body carries.
    """
    db.stores["strategy_versions"].append(_saved_version_row(strategy_id))
    rows = _three_eligible_backtests(owner_id, strategy_id)
    db.stores["strategy_backtests"].extend(rows)
    return [r["id"] for r in rows]


class _NoopAuditLogger:
    """A stand-in for the strategy audit logger used only in the submission-flow tests.

    The Eligibility_Gate writes one audit record on every evaluation via
    ``record_or_raise`` (Requirement 2.11), which in a live process pushes to Redis. There is
    no Redis in this test environment — exactly as there is no Supabase — so this double keeps
    the audit write a no-op, isolating the tests from infrastructure the same way ``FakeDB``
    isolates them from the database. It asserts nothing about auditing; that is covered by the
    gate's own unit tests.
    """

    async def record_or_raise(self, *args, **kwargs):
        return None

    async def log(self, *args, **kwargs):
        return None


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def db():
    """
    Returns a fresh FakeDB pre-seeded with:
      - One strategy owned by AUTHOR_ID with a valid backtest_result and
        a buy_logic that contains one action node (required by publish_strategy).
    """
    _db = FakeDB()
    _db.stores["strategies"].append(
        {
            "id": STRATEGY_UUID,
            "user_id": AUTHOR_ID,
            "name": "E2E Test Strategy",
            "symbol": "BTC/USD",
            "timeframe": "1h",
            "exchange_id": "binance",
            "buy_logic": json.dumps(
                {"nodes": [{"type": "action", "label": "BUY"}]}
            ),
            "sell_logic": json.dumps(
                {"nodes": [{"type": "action", "label": "SELL"}]}
            ),
            "risk": {"stop_loss_pct": 2},
            "indicators": [],
            "ml_model_path": None,
            "backtest_result": {
                    "total_return_pct": 85.0,
                    "sharpe_ratio": 2.5,
                    "max_drawdown_pct": -3.5,
                    "win_rate_pct": 72.0,
                    "total_trades": 200,
                },
        }
    )
    _db.stores["profiles"].append(
        {
            "id": AUTHOR_ID,
            "display_name": "Test Author",
            "email": "author@test.io",
        }
    )
    return _db


@pytest.fixture
def patched_db(db):
    """Patches _build_service_client to return the in-memory FakeDB."""
    with patch(
        "backend_app.routers.library._build_service_client", return_value=db
    ):
        yield db


@pytest.fixture
def patched_audit():
    """Replace the strategy audit logger with an in-process no-op for submission-flow tests.

    ``eligibility_gate.evaluate`` (via ``record_or_raise``) and
    ``submission_service.create_submission`` both fetch the singleton logger through
    ``backend_app.core.audit_trail.get_strategy_audit_logger``; patching it there covers both
    call sites without touching production code.
    """
    with patch(
        "backend_app.core.audit_trail.get_strategy_audit_logger",
        return_value=_NoopAuditLogger(),
    ):
        yield


@pytest.fixture(autouse=True)
def reset_overrides():
    """Ensures dependency overrides are cleared after every test."""
    yield
    app.dependency_overrides.clear()


# ══════════════════════════════════════════════════════════════════════════════
# 1. P0-1 REGRESSION: get_admin_user behaviour (pure unit tests, no HTTP)
# ══════════════════════════════════════════════════════════════════════════════

import asyncio


def _run_coroutine(coro):
    """Run ``coro`` to completion **without leaving the thread without an event loop**.

    Not ``asyncio.get_event_loop().run_until_complete(...)``: that is deprecated and raises
    ``RuntimeError: There is no current event loop in thread 'MainThread'`` when an earlier
    module in the same session left the thread with no current loop (``asyncio.run`` does
    exactly that — it closes its loop and then calls ``set_event_loop(None)``). And not
    ``asyncio.run`` either, for the same reason in reverse: it would break the neighbours that
    still use the deprecated call.

    Lifted from ``tests/test_checkout_service.py::_run`` (itself lifted from
    ``tests/test_marketplace_checkout_regression.py::_run_coroutine``), which is where the
    reasoning was first recorded: the loop that was current is put back, or a fresh usable one
    installed, before returning.
    """
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


class TestGetAdminUserP01Regression:
    """Confirm the P0-1 fix is in place: get_admin_user reads app_metadata.role."""

    def _run(self, coro):
        return _run_coroutine(coro)

    def test_admin_via_app_metadata_allowed(self):
        user = {
            "id": "admin-uuid",
            "role": "authenticated",
            "app_metadata": {"role": "admin"},
            "user_metadata": {},
        }
        result = self._run(get_admin_user(user))
        assert result["id"] == "admin-uuid"

    def test_admin_via_user_metadata_rejected(self):
        # Phase 7B F-02: user_metadata is client-writable, must not grant admin
        user = {
            "id": "admin-uuid-2",
            "role": "authenticated",
            "app_metadata": {},
            "user_metadata": {"role": "admin"},
        }
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            self._run(get_admin_user(user))
        assert exc_info.value.status_code == 403

    def test_non_admin_rejected_with_403(self):
        user = {
            "id": "regular-user",
            "role": "authenticated",
            "app_metadata": {"role": "user"},
            "user_metadata": {},
        }
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            self._run(get_admin_user(user))
        assert exc_info.value.status_code == 403

    def test_missing_metadata_rejected_gracefully(self):
        user = {
            "id": "bare-user",
            "role": "authenticated",
            # No app_metadata / user_metadata keys at all
        }
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            self._run(get_admin_user(user))
        assert exc_info.value.status_code == 403


# ══════════════════════════════════════════════════════════════════════════════
# 2. FULL END-TO-END PIPELINE TEST
# ══════════════════════════════════════════════════════════════════════════════

class TestMarketplacePipeline:
    """
    Exercises the complete publish → moderate → browse → clone → rate pipeline.

    Each test uses app.dependency_overrides to inject the appropriate user
    without needing live JWT/Supabase authentication, exactly as test_admin_auth.py does.
    """

    def test_01_submit_strategy_with_three_backtests(self, patched_db, patched_audit):
        """Owner publishes via POST /api/library/submissions with three backtest ids (task 14.10).

        The publish path is no longer ``POST /api/library`` admitting on one
        ``strategies.backtest_result`` blob and returning ``suggested_price``/``eval_score``. It
        is the Eligibility_Gate flow: three completed, distinct Backtest_Evidence runs of a
        saved version. On admission a ``marketplace_submissions`` row is created and advanced to
        SUBMITTED (Requirements 2.12, 3.1); nothing about a price or an evaluation score is
        returned.
        """
        backtest_ids = _seed_eligible_submission(patched_db, AUTHOR_ID, STRATEGY_UUID)
        with_user(AUTHOR_USER)

        resp = client.post(
            "/api/library/submissions",
            json={"strategy_id": STRATEGY_UUID, "backtest_ids": backtest_ids},
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()

        # The response is a Submission identifier and its state — not suggested_price/eval_score.
        assert "submission_id" in body
        assert body["submission_state"] == "SUBMITTED"
        assert "suggested_price" not in body
        assert "eval_score" not in body
        assert "evaluation_score" not in body

        # DB reflects the created Submission, advanced to SUBMITTED and owned by the author.
        subs = patched_db.stores["marketplace_submissions"]
        assert len(subs) == 1
        assert subs[0]["submission_state"] == "SUBMITTED"
        assert subs[0]["owner_id"] == AUTHOR_ID
        assert subs[0]["source_strategy_id"] == STRATEGY_UUID

        # The immutable evidence copy carries one row per referenced backtest (Requirement 3.9).
        evidence = patched_db.stores["marketplace_backtest_evidence"]
        assert len(evidence) == len(backtest_ids) == 3
        # The DRAFT->SUBMITTED transition was recorded (Requirement 4.13).
        transitions = patched_db.stores["marketplace_submission_transitions"]
        assert any(
            t["from_state"] == "DRAFT" and t["to_state"] == "SUBMITTED"
            for t in transitions
        )

    def test_02_admin_pending_queue_shows_published_strategy(self, patched_db):
        """Admin pending queue returns the newly published strategy."""
        lib_id = str(uuid.uuid4())
        patched_db.stores["library_strategies"].append(
            _pending_lib_row(lib_id, AUTHOR_ID, STRATEGY_UUID)
        )
        with_user(ADMIN_USER)

        resp = client.get(
            "/api/library/admin/pending",
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["id"] == lib_id
        assert body["items"][0]["moderation_status"] == "pending"

    def test_03_non_admin_cannot_access_pending_queue(self, patched_db):
        """Non-admin user receives 403 from the pending queue endpoint."""
        with_user(CLONER_USER)
        resp = client.get(
            "/api/library/admin/pending",
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 403

    def test_04_admin_approves_strategy(self, patched_db, patched_audit):
        """Admin approves via the submission action; the legacy PATCH cannot (task 14.5/14.4).

        The lifecycle move is no longer ``PATCH /api/library/admin/{id}`` writing
        ``moderation_status='approved'`` directly. ``admin_moderate_strategy`` is narrowed
        (task 14.5): for a listing whose open Submission projects a different
        ``moderation_status`` than the one supplied, the retained PATCH refuses with 409
        ``MARKETPLACE_USE_SUBMISSION_ACTIONS`` — it can no longer move the effective lifecycle
        behind the state machine's back (Requirements 4.2, 4.12). Approval now runs through the
        task-14.4 admin action ``POST /api/library/admin/submissions/{id}/approve``, which walks
        ``SUBMITTED → UNDER_REVIEW → APPROVED`` and records each edge (Requirement 4.13).
        """
        lib_id = str(uuid.uuid4())
        sub_id = str(uuid.uuid4())
        patched_db.stores["library_strategies"].append(
            _pending_lib_row(lib_id, AUTHOR_ID, STRATEGY_UUID)
        )
        # The listing has an open Submission in SUBMITTED — its projected moderation_status is
        # 'pending' (MODERATION_STATUS_FOR_STATE[SUBMITTED]).
        patched_db.stores["marketplace_submissions"].append(
            _open_submission_row(STRATEGY_UUID, AUTHOR_ID, "SUBMITTED", submission_id=sub_id)
        )
        with_user(ADMIN_USER)

        # The retained PATCH may NOT drive the lifecycle: 'approved' disagrees with the open
        # Submission's projected 'pending', so it is refused with 409 (the core of task 14.5).
        refused = client.patch(
            f"/api/library/admin/{lib_id}",
            json={
                "moderation_status": "approved",
                "moderation_notes": "Looks good.",
            },
            headers={"Authorization": "Bearer test-token"},
        )
        assert refused.status_code == 409, refused.text
        assert refused.json()["error"]["code"] == "MARKETPLACE_USE_SUBMISSION_ACTIONS"
        # The refusal changed nothing: the Submission is still SUBMITTED.
        assert patched_db.stores["marketplace_submissions"][0]["submission_state"] == "SUBMITTED"

        # Approval runs through the task-14.4 submission action instead.
        approved = client.post(
            f"/api/library/admin/submissions/{sub_id}/approve",
            headers={"Authorization": "Bearer test-token"},
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["submission_state"] == "APPROVED"

        # The Submission row advanced to APPROVED, and both edges of the two-step were recorded.
        sub_row = patched_db.stores["marketplace_submissions"][0]
        assert sub_row["submission_state"] == "APPROVED"
        transitions = patched_db.stores["marketplace_submission_transitions"]
        assert any(
            t["from_state"] == "SUBMITTED" and t["to_state"] == "UNDER_REVIEW"
            for t in transitions
        )
        assert any(
            t["from_state"] == "UNDER_REVIEW" and t["to_state"] == "APPROVED"
            for t in transitions
        )

    def test_05_pending_queue_empty_after_approval(self, patched_db, patched_audit):
        """After the Submission is published, the admin pending queue is empty (task 14.4).

        The pending queue reads ``library_strategies.moderation_status == 'pending'``. The
        listing leaves that queue only once its Submission reaches ``PUBLISHED``, whose projected
        value is ``'approved'`` (``MODERATION_STATUS_FOR_STATE[PUBLISHED]``); an ``APPROVED``
        Submission still projects ``'pending'`` and stays in the queue (Requirement 4.7). The
        lifecycle move now runs through the task-14.4 admin actions
        (``approve`` then ``publish``), not the narrowed PATCH.
        """
        lib_id = str(uuid.uuid4())
        sub_id = str(uuid.uuid4())
        row = _pending_lib_row(lib_id, AUTHOR_ID, STRATEGY_UUID)
        patched_db.stores["library_strategies"].append(row)
        patched_db.stores["marketplace_submissions"].append(
            _open_submission_row(STRATEGY_UUID, AUTHOR_ID, "SUBMITTED", submission_id=sub_id)
        )
        with_user(ADMIN_USER)

        # Drive the Submission SUBMITTED → APPROVED → PUBLISHED via the admin actions.
        approve = client.post(
            f"/api/library/admin/submissions/{sub_id}/approve",
            headers={"Authorization": "Bearer test-token"},
        )
        assert approve.status_code == 200, approve.text
        publish = client.post(
            f"/api/library/admin/submissions/{sub_id}/publish",
            headers={"Authorization": "Bearer test-token"},
        )
        assert publish.status_code == 200, publish.text
        assert publish.json()["submission_state"] == "PUBLISHED"

        # In production the AFTER-UPDATE trigger trg_submission_projects_moderation_status
        # projects MODERATION_STATUS_FOR_STATE[PUBLISHED] == 'approved' onto the listing. The
        # in-memory FakeDB has no triggers, so apply that same one shared projection here — this
        # is the projection under test, not an independent write.
        from backend_app.backend.marketplace.submission_state import (
            MODERATION_STATUS_FOR_STATE,
            SubmissionState,
        )
        row["moderation_status"] = MODERATION_STATUS_FOR_STATE[SubmissionState.PUBLISHED]

        # Pending queue must now be empty.
        resp = client.get(
            "/api/library/admin/pending",
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_06_browse_shows_approved_not_pending(self, patched_db):
        """browse_library returns approved strategies and excludes pending ones."""
        approved_id = str(uuid.uuid4())
        pending_id = str(uuid.uuid4())
        patched_db.stores["library_strategies"].extend([
            _approved_lib_row(approved_id, AUTHOR_ID, STRATEGY_UUID),
            _pending_lib_row(pending_id, AUTHOR_ID, str(uuid.uuid4())),
        ])

        # browse_library does not require auth
        resp = client.get("/api/library")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # The catalogue now returns the public projection (task 16.2), which represents the
        # Listing id as `listing_id` and never leaks `id`/`author_id` (Requirements 6.1, 6.2).
        returned_ids = {s["listing_id"] for s in body["items"]}
        assert approved_id in returned_ids
        assert pending_id not in returned_ids

    def test_07_clone_approved_strategy_succeeds(self, patched_db):
        """A different user (user-cloner) can clone an approved strategy (task 17.2/17.8).

        Cloning copies the owner's Protected_Logic into a row the caller owns, so it now takes
        **two** owner-side conditions, not one: the owner's explicit consent
        (``library_strategies.source_cloning_enabled``, Requirements 7.3, 7.4 — added by
        migration 007 defaulted ``FALSE``) *and* an entitling Subscription resolved by
        ``entitlement_resolver.resolve`` (Requirement 7.2). The fixture therefore sets both, and
        seeds the saved Strategy_Version the resolver requires ``source_strategy_id`` to resolve
        to (Requirement 7.11).

        The reason is asserted directly against the resolver first, so this test's premise —
        "the cloner is entitled" — is established by the single admission decision itself rather
        than assumed from a 201.
        """
        lib_id = str(uuid.uuid4())
        patched_db.stores["library_strategies"].append(
            _cloneable_lib_row(
                lib_id, AUTHOR_ID, STRATEGY_UUID, subscriber_id=CLONER_ID
            )
        )
        # The Listing's backing strategy must resolve to a saved, non-draft Strategy_Version or
        # the resolver answers LISTING_UNAVAILABLE (409) rather than SUBSCRIBED.
        patched_db.stores["strategy_versions"].append(_saved_version_row(STRATEGY_UUID))

        # Premise check: the fixture is entitling, with reason SUBSCRIBED.
        from backend_app.backend.marketplace import entitlement_resolver

        entitlement = _run_coroutine(
            entitlement_resolver.resolve(
                {"id": CLONER_ID}, lib_id, patched_db, datetime.now(timezone.utc)
            )
        )
        assert entitlement.entitling is True
        assert entitlement.reason is entitlement_resolver.EntitlementReason.SUBSCRIBED

        with_user(CLONER_USER)

        resp = client.post(
            f"/api/library/{lib_id}/clone",
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert "new_strategy_id" in body

        # clone_count incremented
        lib_row = patched_db.stores["library_strategies"][0]
        assert lib_row["clone_count"] == 1

        # Verified-clone marker created in library_ratings
        rating_rows = patched_db.stores["library_ratings"]
        clone_marker = next(
            (r for r in rating_rows if r["library_id"] == lib_id and r["user_id"] == CLONER_ID),
            None,
        )
        assert clone_marker is not None
        assert clone_marker["is_verified_clone"] is True

    def test_07b_clone_refused_when_source_cloning_is_disabled(self, patched_db):
        """The default-disabled owner-consent path refuses the clone (task 17.2/17.8).

        The companion to test 07: identical fixture in every respect *except* that
        ``source_cloning_enabled`` carries its migration-007 default of ``FALSE`` — the state
        every Listing is in until its owner calls ``PATCH /api/library/{id}/settings``. The
        cloner still holds the same entitling Subscription, so this isolates the consent gate
        from the entitlement gate: consent alone is enough to refuse (Requirement 7.3).

        The refusal is 403 ``MARKETPLACE_CLONING_DISABLED`` and it happens **before anything is
        written** (Requirement 7.4): no ``strategies`` row is created, ``clone_count`` is
        unchanged, and no verified-clone marker appears in ``library_ratings``.
        """
        lib_id = str(uuid.uuid4())
        patched_db.stores["library_strategies"].append(
            _cloneable_lib_row(
                lib_id,
                AUTHOR_ID,
                STRATEGY_UUID,
                subscriber_id=CLONER_ID,
                source_cloning_enabled=False,
            )
        )
        patched_db.stores["strategy_versions"].append(_saved_version_row(STRATEGY_UUID))

        # The only seeded strategy is the author's own; a refused clone must not add to it.
        strategies_before = len(patched_db.stores["strategies"])
        with_user(CLONER_USER)

        resp = client.post(
            f"/api/library/{lib_id}/clone",
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["error"]["code"] == "MARKETPLACE_CLONING_DISABLED"

        # No `strategies` row created — neither a clone of this listing nor any other row.
        assert len(patched_db.stores["strategies"]) == strategies_before
        assert not [
            s
            for s in patched_db.stores["strategies"]
            if str(s.get("source_library_id")) == lib_id
        ]

        # clone_count unchanged, and no verified-clone marker written.
        lib_row = patched_db.stores["library_strategies"][0]
        assert lib_row["clone_count"] == 0
        assert not [
            r
            for r in patched_db.stores["library_ratings"]
            if str(r.get("library_id")) == lib_id
        ]

    def test_08_self_clone_prevented(self, patched_db):
        """Author cannot clone their own published strategy (409 Conflict)."""
        lib_id = str(uuid.uuid4())
        patched_db.stores["library_strategies"].append(
            _approved_lib_row(lib_id, AUTHOR_ID, STRATEGY_UUID)
        )
        with_user(AUTHOR_USER)

        resp = client.post(
            f"/api/library/{lib_id}/clone",
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 409, resp.text
        assert "cannot clone your own" in resp.text.lower()

    def test_09_clone_pending_strategy_rejected(self, patched_db):
        """Cloning a pending strategy is rejected (not available for cloning — 404)."""
        lib_id = str(uuid.uuid4())
        patched_db.stores["library_strategies"].append(
            _pending_lib_row(lib_id, AUTHOR_ID, STRATEGY_UUID)
        )
        with_user(CLONER_USER)

        resp = client.post(
            f"/api/library/{lib_id}/clone",
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 404, resp.text

    def test_10_clone_rejected_strategy_rejected(self, patched_db):
        """Cloning a rejected (is_active=False) strategy is rejected (404)."""
        lib_id = str(uuid.uuid4())
        row = _approved_lib_row(lib_id, AUTHOR_ID, STRATEGY_UUID)
        row["moderation_status"] = "rejected"
        row["is_active"] = False
        patched_db.stores["library_strategies"].append(row)
        with_user(CLONER_USER)

        resp = client.post(
            f"/api/library/{lib_id}/clone",
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 404, resp.text

    def test_11_rate_strategy_after_cloning(self, patched_db):
        """Verified clone user (user-cloner) can rate the strategy."""
        lib_id = str(uuid.uuid4())
        patched_db.stores["library_strategies"].append(
            _approved_lib_row(lib_id, AUTHOR_ID, STRATEGY_UUID)
        )
        # Pre-seed verified-clone marker (set during the clone step)
        patched_db.stores["library_ratings"].append(
            {
                "id": str(uuid.uuid4()),
                "library_id": lib_id,
                "user_id": CLONER_ID,
                "is_verified_clone": True,
                "rating": None,
                "review_text": None,
            }
        )
        with_user(CLONER_USER)

        resp = client.post(
            f"/api/library/{lib_id}/rate",
            json={"rating": 5, "review_text": "Excellent strategy!"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["rating"] == 5
        assert body["library_id"] == lib_id

    def test_12_rate_strategy_without_cloning_rejected(self, patched_db):
        """Non-cloner cannot rate — must clone first (403)."""
        lib_id = str(uuid.uuid4())
        patched_db.stores["library_strategies"].append(
            _approved_lib_row(lib_id, AUTHOR_ID, STRATEGY_UUID)
        )
        # No verified-clone marker for CLONER_ID (intentionally omitted to test rejection)
        with_user(CLONER_USER)

        resp = client.post(
            f"/api/library/{lib_id}/rate",
            json={"rating": 4, "review_text": "Good."},
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 403, resp.text

    def test_13_unpublish_removes_from_pool(self, patched_db):
        """Author can unpublish; the strategy is no longer browsable or cloneable."""
        lib_id = str(uuid.uuid4())
        patched_db.stores["library_strategies"].append(
            _approved_lib_row(lib_id, AUTHOR_ID, STRATEGY_UUID)
        )
        with_user(AUTHOR_USER)

        # Unpublish
        resp = client.delete(
            f"/api/library/{lib_id}",
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 200, resp.text

        # Browse: should return 0 approved active results
        browse_resp = client.get("/api/library")
        assert browse_resp.json()["total"] == 0

        # Clone: should fail (is_active=False)
        with_user(CLONER_USER)
        clone_resp = client.post(
            f"/api/library/{lib_id}/clone",
            headers={"Authorization": "Bearer test-token"},
        )
        assert clone_resp.status_code == 404

    def test_14_submit_with_open_submission_rejected(self, patched_db, patched_audit):
        """Submitting a strategy that already has an open Submission is refused (task 14.10).

        Publishing the same strategy twice is now caught by the Eligibility_Gate's
        MP_SUBMISSION_OPEN criterion (Requirement 2.7): an existing open Submission for the
        strategy means the gate does not admit, so the response is a 422 eligibility failure
        naming MP_SUBMISSION_OPEN and NO second ``marketplace_submissions`` row is created.
        """
        backtest_ids = _seed_eligible_submission(patched_db, AUTHOR_ID, STRATEGY_UUID)
        # An open Submission already occupies the one-open-Submission-per-strategy slot.
        patched_db.stores["marketplace_submissions"].append(
            {
                "id": str(uuid.uuid4()),
                "source_strategy_id": STRATEGY_UUID,
                "owner_id": AUTHOR_ID,
                "submission_state": "SUBMITTED",
            }
        )
        with_user(AUTHOR_USER)

        resp = client.post(
            "/api/library/submissions",
            json={"strategy_id": STRATEGY_UUID, "backtest_ids": backtest_ids},
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert body["error"]["code"] == "MARKETPLACE_ELIGIBILITY_FAILED"
        assert "MP_SUBMISSION_OPEN" in body["error"]["details"]["failures"]

        # No new Submission was created — the pre-seeded open one is the only row.
        assert len(patched_db.stores["marketplace_submissions"]) == 1

    def test_15_submit_without_valid_backtest_evidence_rejected(self, patched_db, patched_audit):
        """A submission whose referenced runs are not valid evidence is refused (task 14.10).

        The old ``publish_strategy`` admitted on one ``strategies.backtest_result`` blob and
        rejected only its absence. The new flow admits on three completed, owner-scoped
        ``strategy_backtests`` runs; a strategy with a saved version but whose referenced runs
        do not exist as the owner's completed runs is refused by the Eligibility_Gate with a 422
        naming the failed criteria (Requirements 2.4, 2.5, 3.1) — and NO Submission is created.
        """
        no_bt_id = str(uuid.uuid4())
        patched_db.stores["strategies"].append(
            {
                "id": no_bt_id,
                "user_id": AUTHOR_ID,
                "name": "No Valid Evidence",
                "symbol": "ETH/USD",
                "timeframe": "1d",
                "exchange_id": "binance",
            }
        )
        # A saved version exists, so MP_VERSION_* pass — the failure is purely the evidence.
        patched_db.stores["strategy_versions"].append(_saved_version_row(no_bt_id))
        # Three referenced backtest ids that are not the owner's completed runs (none seeded):
        # the owner-scoped read returns nothing, exactly as it would for a foreign or absent run.
        phantom_backtest_ids = [str(uuid.uuid4()) for _ in range(3)]
        with_user(AUTHOR_USER)

        resp = client.post(
            "/api/library/submissions",
            json={"strategy_id": no_bt_id, "backtest_ids": phantom_backtest_ids},
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert body["error"]["code"] == "MARKETPLACE_ELIGIBILITY_FAILED"
        failures = body["error"]["details"]["failures"]
        # With no completed evidence, execution success and metric completeness cannot hold.
        assert "MP_EXECUTION_OK" in failures
        assert "MP_METRICS_COMPLETE" in failures

        # Nothing was persisted — no Submission on a rejected evaluation (Requirement 2.10).
        assert patched_db.stores["marketplace_submissions"] == []

    def test_16_submit_with_invalid_version_rejected(self, patched_db, patched_audit):
        """A submission whose saved version does not pass validation is refused (task 14.10).

        The old ``publish_strategy`` rejected a strategy whose DAG carried no action node. The
        new flow expresses the same "the strategy itself is not fit to publish" refusal through
        the Eligibility_Gate's MP_VERSION_VALID criterion (Requirement 2.3): the strategy's only
        saved version does not pass the Strategy_Builder's structural validation, so the gate
        does not admit — a 422 naming MP_VERSION_VALID, and NO Submission created. The referenced
        backtests are all valid here, isolating the failure to the version.
        """
        bad_version_id = str(uuid.uuid4())
        patched_db.stores["strategies"].append(
            {
                "id": bad_version_id,
                "user_id": AUTHOR_ID,
                "name": "Invalid Version",
                "symbol": "ETH/USD",
                "timeframe": "1d",
                "exchange_id": "binance",
            }
        )
        # A saved (non-draft) version whose stored verdict is not VALID and whose canonical
        # graph cannot be parsed (an unsupported schema version), so _version_graph_is_valid
        # returns False and MP_VERSION_VALID fails.
        patched_db.stores["strategy_versions"].append(
            {
                "id": VERSION_UUID,
                "strategy_id": bad_version_id,
                "version": 1,
                "is_draft": False,
                "validation_state": "INVALID",
                "blueprint": None,
                "graph_json": {"schema_version": 99, "nodes": [], "edges": []},
            }
        )
        # Valid backtest evidence referencing that same version, so only the version is at fault.
        backtest_rows = _three_eligible_backtests(AUTHOR_ID, bad_version_id)
        patched_db.stores["strategy_backtests"].extend(backtest_rows)
        backtest_ids = [r["id"] for r in backtest_rows]
        with_user(AUTHOR_USER)

        resp = client.post(
            "/api/library/submissions",
            json={"strategy_id": bad_version_id, "backtest_ids": backtest_ids},
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert body["error"]["code"] == "MARKETPLACE_ELIGIBILITY_FAILED"
        assert "MP_VERSION_VALID" in body["error"]["details"]["failures"]

        # A rejected evaluation creates no Submission (Requirement 2.10).
        assert patched_db.stores["marketplace_submissions"] == []

    def test_17_non_admin_cannot_moderate(self, patched_db):
        """Non-admin user cannot call the retained moderation endpoint (403).

        Unaffected by the task-14.5 narrowing: authorization via ``get_admin_user`` runs before
        the ``moderation_status``-agreement guard, so a non-admin is refused with 403 regardless
        of what ``moderation_status`` (or open Submission) is involved — the guard is never
        reached. An open Submission is seeded to prove the 403 wins even when the narrowing guard
        would otherwise have something to say.
        """
        lib_id = str(uuid.uuid4())
        patched_db.stores["library_strategies"].append(
            _pending_lib_row(lib_id, AUTHOR_ID, STRATEGY_UUID)
        )
        patched_db.stores["marketplace_submissions"].append(
            _open_submission_row(STRATEGY_UUID, AUTHOR_ID, "SUBMITTED")
        )
        with_user(CLONER_USER)

        resp = client.patch(
            f"/api/library/admin/{lib_id}",
            json={"moderation_status": "approved"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 403

    def test_18_admin_can_feature_strategy(self, patched_db):
        """The retained PATCH still sets is_featured and moderation_notes (task 14.5).

        Featuring stays exactly what it is today — ``library_strategies.is_featured``, set by
        the retained ``admin_moderate_strategy`` — and is orthogonal to the Submission lifecycle
        (design.md § The mapping). ``'featured'`` is reachable only for a PUBLISHED listing, so
        the listing here has a PUBLISHED open Submission whose projected ``moderation_status`` is
        ``'approved'`` (``MODERATION_STATUS_FOR_STATE[PUBLISHED]``). Supplying that agreeing value
        passes the task-14.5 guard, so the route is free to do the one thing it is retained for:
        set ``is_featured`` (and ``moderation_notes``) without moving the lifecycle.
        """
        lib_id = str(uuid.uuid4())
        row = _pending_lib_row(lib_id, AUTHOR_ID, STRATEGY_UUID)
        row["moderation_status"] = "approved"  # the projection of the PUBLISHED Submission
        patched_db.stores["library_strategies"].append(row)
        patched_db.stores["marketplace_submissions"].append(
            _open_submission_row(STRATEGY_UUID, AUTHOR_ID, "PUBLISHED")
        )
        with_user(ADMIN_USER)

        resp = client.patch(
            f"/api/library/admin/{lib_id}",
            json={
                "moderation_status": "approved",
                "is_featured": True,
                "moderation_notes": "Editor's pick.",
            },
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 200, resp.text

        row = patched_db.stores["library_strategies"][0]
        assert row.get("is_featured") is True
        assert row.get("moderation_notes") == "Editor's pick."
