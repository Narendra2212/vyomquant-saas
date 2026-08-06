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
from typing import Any, Dict, Optional
from unittest.mock import patch

import pytest

# ─── Path and environment bootstrapping ───────────────────────────────────────
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient

from backend_app.core.dependencies import get_admin_user, get_current_user
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
    """Override get_current_user to return the given user dict."""
    app.dependency_overrides[get_current_user] = lambda: user_dict


def clear_overrides():
    app.dependency_overrides.clear()


# ─── Shared strategy IDs ──────────────────────────────────────────────────────

STRATEGY_UUID = str(uuid.uuid4())


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
        }

    def table(self, name: str) -> FakeTable:
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
            "risk": json.dumps({"stop_loss_pct": 2}),
            "indicators": json.dumps([]),
            "ml_model_path": None,
            "backtest_result": json.dumps(
                {
                    "total_return_pct": 42.5,
                    "sharpe_ratio": 1.8,
                    "max_drawdown_pct": -5.2,
                    "win_rate_pct": 61.0,
                    "total_trades": 120,
                }
            ),
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


@pytest.fixture(autouse=True)
def reset_overrides():
    """Ensures dependency overrides are cleared after every test."""
    yield
    app.dependency_overrides.clear()


# ══════════════════════════════════════════════════════════════════════════════
# 1. P0-1 REGRESSION: get_admin_user behaviour (pure unit tests, no HTTP)
# ══════════════════════════════════════════════════════════════════════════════

import asyncio


class TestGetAdminUserP01Regression:
    """Confirm the P0-1 fix is in place: get_admin_user reads app_metadata.role."""

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def test_admin_via_app_metadata_allowed(self):
        user = {
            "id": "admin-uuid",
            "role": "authenticated",
            "app_metadata": {"role": "admin"},
            "user_metadata": {},
        }
        result = self._run(get_admin_user(user))
        assert result["id"] == "admin-uuid"

    def test_admin_via_user_metadata_allowed(self):
        user = {
            "id": "admin-uuid-2",
            "role": "authenticated",
            "app_metadata": {},
            "user_metadata": {"role": "admin"},
        }
        result = self._run(get_admin_user(user))
        assert result["id"] == "admin-uuid-2"

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

    def test_01_publish_strategy(self, patched_db):
        """User-author publishes a valid strategy; it lands in 'pending'."""
        with_user(AUTHOR_USER)
        resp = client.post(
            "/api/library",
            json={
                "strategy_id": STRATEGY_UUID,
                "description": "A solid momentum play.",
                "category": "momentum",
                "difficulty": "intermediate",
                "tags": ["crypto", "btc"],
            },
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert "library_id" in body
        assert body["moderation_status"] == "pending"

        # DB reflects pending status
        lib_rows = patched_db.stores["library_strategies"]
        assert len(lib_rows) == 1
        assert lib_rows[0]["moderation_status"] == "pending"
        assert lib_rows[0]["is_active"] is True
        assert lib_rows[0]["author_id"] == AUTHOR_ID

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

    def test_04_admin_approves_strategy(self, patched_db):
        """Admin moderates a pending strategy to 'approved'."""
        lib_id = str(uuid.uuid4())
        patched_db.stores["library_strategies"].append(
            _pending_lib_row(lib_id, AUTHOR_ID, STRATEGY_UUID)
        )
        with_user(ADMIN_USER)

        resp = client.patch(
            f"/api/library/admin/{lib_id}",
            json={
                "moderation_status": "approved",
                "moderation_notes": "Looks good.",
            },
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["moderation_status"] == "approved"

        # DB row updated
        row = patched_db.stores["library_strategies"][0]
        assert row["moderation_status"] == "approved"
        assert row["moderated_by"] == ADMIN_ID

    def test_05_pending_queue_empty_after_approval(self, patched_db):
        """After approval, admin pending queue returns empty list."""
        lib_id = str(uuid.uuid4())
        row = _pending_lib_row(lib_id, AUTHOR_ID, STRATEGY_UUID)
        patched_db.stores["library_strategies"].append(row)
        with_user(ADMIN_USER)

        # Approve it
        client.patch(
            f"/api/library/admin/{lib_id}",
            json={"moderation_status": "approved"},
            headers={"Authorization": "Bearer test-token"},
        )

        # Pending queue must now be empty
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
        returned_ids = {s["id"] for s in body["items"]}
        assert approved_id in returned_ids
        assert pending_id not in returned_ids

    def test_07_clone_approved_strategy_succeeds(self, patched_db):
        """A different user (user-cloner) can clone an approved strategy."""
        lib_id = str(uuid.uuid4())
        patched_db.stores["library_strategies"].append(
            _approved_lib_row(lib_id, AUTHOR_ID, STRATEGY_UUID)
        )
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

    def test_14_publish_duplicate_rejected(self, patched_db):
        """Publishing the same strategy twice is rejected with 409."""
        lib_id = str(uuid.uuid4())
        # Already has an active publication
        patched_db.stores["library_strategies"].append(
            {
                "id": lib_id,
                "source_strategy_id": STRATEGY_UUID,
                "is_active": True,
                "moderation_status": "pending",
            }
        )
        with_user(AUTHOR_USER)

        resp = client.post(
            "/api/library",
            json={
                "strategy_id": STRATEGY_UUID,
                "description": "Duplicate attempt",
                "category": "momentum",
                "difficulty": "beginner",
                "tags": [],
            },
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 409, resp.text

    def test_15_publish_strategy_without_backtest_rejected(self, patched_db):
        """publish_strategy rejects strategies with no backtest_result (400)."""
        no_bt_id = str(uuid.uuid4())
        patched_db.stores["strategies"].append(
            {
                "id": no_bt_id,
                "user_id": AUTHOR_ID,
                "name": "No Backtest",
                "symbol": "ETH/USD",
                "timeframe": "1d",
                "exchange_id": "binance",
                "buy_logic": json.dumps({"nodes": [{"type": "action"}]}),
                "sell_logic": json.dumps({"nodes": []}),
                "risk": None,
                "indicators": None,
                "ml_model_path": None,
                "backtest_result": None,  # ← no backtest
            }
        )
        with_user(AUTHOR_USER)

        resp = client.post(
            "/api/library",
            json={
                "strategy_id": no_bt_id,
                "description": "No backtest",
                "category": "other",
                "difficulty": "beginner",
                "tags": [],
            },
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 400, resp.text
        assert "backtest" in resp.text.lower()

    def test_16_publish_strategy_without_action_node_rejected(self, patched_db):
        """publish_strategy rejects strategies whose DAG has no action nodes (400)."""
        no_action_id = str(uuid.uuid4())
        patched_db.stores["strategies"].append(
            {
                "id": no_action_id,
                "user_id": AUTHOR_ID,
                "name": "No Action Node",
                "symbol": "ETH/USD",
                "timeframe": "1d",
                "exchange_id": "binance",
                "buy_logic": json.dumps({"nodes": [{"type": "indicator"}]}),
                "sell_logic": json.dumps({"nodes": []}),
                "risk": None,
                "indicators": None,
                "ml_model_path": None,
                "backtest_result": json.dumps({"total_return_pct": 10}),
            }
        )
        with_user(AUTHOR_USER)

        resp = client.post(
            "/api/library",
            json={
                "strategy_id": no_action_id,
                "description": "No action node",
                "category": "other",
                "difficulty": "beginner",
                "tags": [],
            },
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 400, resp.text
        assert "action" in resp.text.lower()

    def test_17_non_admin_cannot_moderate(self, patched_db):
        """Non-admin user cannot call admin moderation endpoint (403)."""
        lib_id = str(uuid.uuid4())
        patched_db.stores["library_strategies"].append(
            _pending_lib_row(lib_id, AUTHOR_ID, STRATEGY_UUID)
        )
        with_user(CLONER_USER)

        resp = client.patch(
            f"/api/library/admin/{lib_id}",
            json={"moderation_status": "approved"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 403

    def test_18_admin_can_feature_strategy(self, patched_db):
        """Admin can set is_featured=True."""
        lib_id = str(uuid.uuid4())
        patched_db.stores["library_strategies"].append(
            _pending_lib_row(lib_id, AUTHOR_ID, STRATEGY_UUID)
        )
        with_user(ADMIN_USER)

        resp = client.patch(
            f"/api/library/admin/{lib_id}",
            json={"moderation_status": "approved", "is_featured": True},
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 200, resp.text

        row = patched_db.stores["library_strategies"][0]
        assert row.get("is_featured") is True
