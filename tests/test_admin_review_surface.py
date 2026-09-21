"""
tests/test_admin_review_surface.py

Task 14.7 of ``marketplace-subscriptions-paper-trading``: the guard on the Admin_Reviewer
surface — the admin list ``GET /api/library/admin/submissions`` and the admin detail
``GET /api/library/admin/submissions/{id}`` (routes: ``library.admin_list_submissions`` /
``library.admin_submission_detail``), plus the transition/audit behaviour of
``submission_service.apply_admin_action``.

WHAT IS ASSERTED (Requirements 5.1, 5.2, 5.3, 5.7, 5.8, 5.9, 5.11)
------------------------------------------------------------------
* ``listing_projection.assert_contains_no_protected_logic`` is applied to BOTH admin
  responses — the list and the detail — with a token set seeded from a strategy's
  blueprint / graph_json / buy_logic / sell_logic, and neither response leaks any of it
  (Requirements 5.8, 6.8).
* The list orders ``submitted_at DESC, id DESC``, clamps ``page_size`` to 100 when a
  larger value is requested (default 25), and returns ``total`` (Requirements 5.1, 5.2).
* A non-Admin_Reviewer gets 403 from ``get_admin_user`` BEFORE any read — the injected db
  records zero calls — and the 403 body reveals nothing about whether the named Submission
  exists (Requirements 5.1, 5.7).
* An unknown Submission (``MARKETPLACE_SUBMISSION_NOT_FOUND``, 404) and a rejected
  transition (``MARKETPLACE_SUBMISSION_TRANSITION_REJECTED``, 409) carry DISTINCT codes
  (Requirement 5.9).
* A failed audit write inside ``apply_admin_action`` rolls the state change back and
  raises ``MARKETPLACE_ACTION_NOT_RECORDED`` (Requirement 5.11).

HARNESS
-------
Two layers, each the one that genuinely exercises what it claims:

* The list, the detail, the page-size clamp, the ordering and the *real* ``get_admin_user``
  403 are tested through a FastAPI ``TestClient``. ``get_current_user`` is overridden per
  test with a user dict (admin or non-admin), so ``get_admin_user`` — which depends on
  ``get_current_user`` — runs for real and raises its own 403 for a non-admin BEFORE the
  route body reads anything. ``library._build_service_client`` is patched to return a
  recording fake db, so "no read happened" is checkable as "the fake recorded zero calls".
* The audit-rollback path is tested by calling ``submission_service.apply_admin_action``
  directly against a fake Supabase double with ``_record_admin_action`` monkeypatched to
  raise — the same double-and-direct-call pattern ``tests/test_submission_service.py`` uses,
  which is where the compensating revert UPDATE is observable.
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient

from backend_app.core.dependencies import get_current_user
from backend_app.main import app
from backend_app.backend.marketplace import listing_projection
from backend_app.backend.marketplace import submission_service as svc
from backend_app.backend.marketplace.submission_service import (
    MARKETPLACE_ACTION_NOT_RECORDED,
    MARKETPLACE_SUBMISSION_NOT_FOUND,
    MARKETPLACE_SUBMISSION_TRANSITION_REJECTED,
    SubmissionAction,
    SubmissionServiceError,
    apply_admin_action,
)

client = TestClient(app, raise_server_exceptions=False)

ADMIN_PREFIX = "/api/library/admin/submissions"


# ══════════════════════════════════════════════════════════════════════════
# Users
# ══════════════════════════════════════════════════════════════════════════

ADMIN_USER = {
    "id": "11111111-1111-1111-1111-111111111111",
    "email": "admin@test.vyomquant.io",
    "role": "authenticated",
    "app_metadata": {"role": "admin"},  # what get_admin_user actually reads
    "user_metadata": {},
}
NON_ADMIN_USER = {
    "id": "22222222-2222-2222-2222-222222222222",
    "email": "user@test.vyomquant.io",
    "role": "authenticated",
    "app_metadata": {"role": "authenticated"},  # not admin/support/operator
    "user_metadata": {},
}

# A Submission id that a non-admin will name — the 403 body must not confirm or deny it.
NAMED_SUBMISSION_ID = "33333333-3333-3333-3333-333333333333"


def _as_admin():
    app.dependency_overrides[get_current_user] = lambda: ADMIN_USER


def _as_non_admin():
    app.dependency_overrides[get_current_user] = lambda: NON_ADMIN_USER


def _clear_overrides():
    app.dependency_overrides.clear()


# ══════════════════════════════════════════════════════════════════════════
# Protected_Logic seeds — the graph a leak would expose
# ══════════════════════════════════════════════════════════════════════════

# A strategy whose Protected_Logic carries node ids, indicator names, thresholds and a
# model path — every token at least PROTECTED_LOGIC_TOKEN_MIN_LENGTH chars, so the
# containment check is meaningful (not vacuously green).
STRATEGY_ROW = {
    "buy_logic": {"node_alpha": {"indicator": "rsi_fast", "threshold": "27.5"}},
    "sell_logic": {"node_omega": {"indicator": "macd_signal", "threshold": "8.125"}},
    "indicators": ["ema_cross_144", "bollinger_squeeze"],
    "risk": {"stop_loss_pct": "3.75"},
    "ml_model_path": "models/secret_lstm_v7/weights.bin",
}
VERSION_ROW = {
    "blueprint": {"nodes": [{"id": "graph_node_zeta", "type": "crossover_detector"}]},
    "graph_json": {"edges": [["graph_node_zeta", "graph_node_theta"]]},
    "execution_graph": {"root": "graph_node_zeta"},
}
BACKTEST_ROWS = [
    {
        "blueprint": {"id": "bt_blueprint_kappa"},
        "dag_hash": "dag_fingerprint_9f3a2b",
        "dataset_checksum": "checksum_c0ffee123",
    }
]

PROTECTED_TOKENS = listing_projection.protected_logic_tokens(
    strategy_row=STRATEGY_ROW,
    version_row=VERSION_ROW,
    backtest_rows=BACKTEST_ROWS,
)


# ══════════════════════════════════════════════════════════════════════════
# A recording fake db for the route-layer reads
# ══════════════════════════════════════════════════════════════════════════


class _Resp:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class _RecordingQuery:
    """Records the fluent chain and returns scripted rows at execute().

    ``op`` is set by the terminal write/read verb. For the admin *list* route the chain is
    ``select(count="exact") … order … order … range … execute``; for the admin *detail*
    route the service issues three ``select … eq … order? … execute`` reads. The query
    remembers its ``order`` calls and ``range`` so the ordering-and-clamp assertions can
    read them back off the recorded call.
    """

    def __init__(self, table: str, db: "RecordingDB"):
        self.table_name = table
        self.db = db
        self.op = ""
        self.select_cols: Any = None
        self.count_mode: Optional[str] = None
        self.filters: List[tuple] = []
        self.orders: List[tuple] = []
        self.range_args: Optional[tuple] = None

    def select(self, cols, count=None):
        self.op = "select"
        self.select_cols = cols
        self.count_mode = count
        return self

    def insert(self, payload):
        self.op = "insert"
        self.payload = payload
        return self

    def update(self, payload):
        self.op = "update"
        self.payload = payload
        return self

    def delete(self):
        self.op = "delete"
        return self

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def in_(self, col, vals):
        self.filters.append((col, list(vals)))
        return self

    def order(self, col, *a, **k):
        self.orders.append((col, k.get("desc", False)))
        return self

    def range(self, start, end):
        self.range_args = (start, end)
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        self.db.calls.append(self)
        key = (self.table_name, self.op)
        script = self.db.script.get(key)
        if script is None:
            return _Resp([], count=0)
        rows = script(self) if callable(script) else script
        count = len(rows) if self.count_mode == "exact" else None
        # For the list route total comes from `count`; honour an explicit override.
        if key in self.db.count_override:
            count = self.db.count_override[key]
        return _Resp(rows, count=count)


class RecordingDB:
    """A fake service-role client that records every executed query."""

    def __init__(self, script=None, count_override=None):
        self.script = script or {}
        self.count_override = count_override or {}
        self.calls: List[_RecordingQuery] = []

    def table(self, name):
        return _RecordingQuery(name, self)

    def ops(self):
        return [(c.table_name, c.op) for c in self.calls]


def _redact_ids(value: Any, ids: set) -> Any:
    """Return ``value`` with any of ``ids`` (the client-supplied path ids) blanked out of
    every string, so two 403 bodies can be compared for everything EXCEPT the id the caller
    itself placed in the request URL."""
    if isinstance(value, str):
        out = value
        for identifier in ids:
            out = out.replace(identifier, "<ID>")
        return out
    if isinstance(value, dict):
        return {k: _redact_ids(v, ids) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_ids(v, ids) for v in value]
    return value


def _list_row(sub_id: str, submitted_at: str, state: str = "SUBMITTED") -> Dict[str, Any]:
    """A ``marketplace_submissions`` list row — the columns the list select names, none of
    them a Protected_Logic column."""
    return {
        "id": sub_id,
        "listing_id": f"listing-{sub_id}",
        "source_strategy_id": f"strat-{sub_id}",
        "owner_id": f"owner-{sub_id}",
        "version_id": f"ver-{sub_id}",
        "submission_state": state,
        "evaluator_version": "eligibility-gate/1.0.0",
        "submitted_at": submitted_at,
        "reviewed_at": None,
        "published_at": None,
        "created_at": submitted_at,
        "updated_at": submitted_at,
    }


# ══════════════════════════════════════════════════════════════════════════
# The admin LIST — ordering, clamp, total, and no Protected_Logic (5.1, 5.2, 5.8)
# ══════════════════════════════════════════════════════════════════════════


class TestAdminList:
    def _db_with_two_rows(self, count_override=25):
        rows = [
            _list_row("bbbb", "2026-07-02T00:00:00+00:00"),
            _list_row("aaaa", "2026-07-01T00:00:00+00:00"),
        ]
        return RecordingDB(
            script={("marketplace_submissions", "select"): rows},
            count_override={("marketplace_submissions", "select"): count_override},
        )

    def test_list_orders_submitted_at_desc_then_id_desc(self):
        db = self._db_with_two_rows()
        _as_admin()
        try:
            with patch(
                "backend_app.routers.library._build_service_client", return_value=db
            ):
                resp = client.get(ADMIN_PREFIX, params={"page": 1, "page_size": 25})
        finally:
            _clear_overrides()

        assert resp.status_code == 200, resp.text
        # The query the route issued ordered submitted_at DESC then id DESC.
        select_call = next(
            c for c in db.calls
            if c.table_name == "marketplace_submissions" and c.op == "select"
        )
        assert select_call.orders == [("submitted_at", True), ("id", True)], (
            f"list must order submitted_at DESC, id DESC; got {select_call.orders}"
        )

    def test_list_clamps_oversized_page_size_to_100(self):
        db = self._db_with_two_rows()
        _as_admin()
        try:
            with patch(
                "backend_app.routers.library._build_service_client", return_value=db
            ):
                resp = client.get(ADMIN_PREFIX, params={"page": 1, "page_size": 500})
        finally:
            _clear_overrides()

        assert resp.status_code == 200, resp.text
        assert resp.json()["page_size"] == 100
        select_call = next(
            c for c in db.calls
            if c.table_name == "marketplace_submissions" and c.op == "select"
        )
        # range(offset, offset + effective_page_size - 1) => (0, 99) for a clamped page.
        assert select_call.range_args == (0, 99), (
            f"a >100 page_size must clamp the range to 100 rows; got {select_call.range_args}"
        )

    def test_list_default_page_size_is_25(self):
        db = self._db_with_two_rows()
        _as_admin()
        try:
            with patch(
                "backend_app.routers.library._build_service_client", return_value=db
            ):
                resp = client.get(ADMIN_PREFIX)  # no page_size => default
        finally:
            _clear_overrides()

        assert resp.status_code == 200, resp.text
        assert resp.json()["page_size"] == 25
        select_call = next(
            c for c in db.calls
            if c.table_name == "marketplace_submissions" and c.op == "select"
        )
        assert select_call.range_args == (0, 24)

    def test_list_returns_total(self):
        db = self._db_with_two_rows(count_override=137)
        _as_admin()
        try:
            with patch(
                "backend_app.routers.library._build_service_client", return_value=db
            ):
                resp = client.get(ADMIN_PREFIX)
        finally:
            _clear_overrides()

        assert resp.status_code == 200, resp.text
        assert resp.json()["total"] == 137

    def test_list_leaks_no_protected_logic(self):
        db = self._db_with_two_rows()
        _as_admin()
        try:
            with patch(
                "backend_app.routers.library._build_service_client", return_value=db
            ):
                resp = client.get(ADMIN_PREFIX)
        finally:
            _clear_overrides()

        assert resp.status_code == 200, resp.text
        # Requirement 5.8 / 6.8: no blueprint/graph_json/buy_logic/etc. token anywhere.
        listing_projection.assert_contains_no_protected_logic(
            resp.json(), PROTECTED_TOKENS
        )


# ══════════════════════════════════════════════════════════════════════════
# The admin DETAIL — reads the immutable evidence copy, no Protected_Logic (5.3, 5.8)
# ══════════════════════════════════════════════════════════════════════════


class TestAdminDetail:
    def _detail_db(self):
        return RecordingDB(
            script={
                ("marketplace_submissions", "select"): [
                    {
                        "id": NAMED_SUBMISSION_ID,
                        "listing_id": "lst-1",
                        "source_strategy_id": "strat-1",
                        "owner_id": "owner-1",
                        "version_id": "ver-1",
                        "submission_state": "UNDER_REVIEW",
                        "eligibility_outcomes": [
                            {"code": "MP_OWNERSHIP", "passed": True}
                        ],
                        "evaluator_version": "eligibility-gate/1.0.0",
                        "rejection_reason": None,
                        "reviewed_by": None,
                        "submitted_at": "2026-07-01T00:00:00+00:00",
                        "reviewed_at": None,
                        "published_at": None,
                        "created_at": "2026-07-01T00:00:00+00:00",
                        "updated_at": "2026-07-01T00:00:00+00:00",
                    }
                ],
                # The immutable evidence copy — outcomes and parameters only, no logic.
                ("marketplace_backtest_evidence", "select"): [
                    {
                        "id": "ev-1",
                        "submission_id": NAMED_SUBMISSION_ID,
                        "source_backtest_id": "bt-0",
                        "condition_index": 0,
                        "dataset": "BTCUSDT",
                        "total_return_pct": "12.5",
                        "max_drawdown_pct": "8.0",
                        "win_rate_pct": "55.0",
                    }
                ],
                ("marketplace_submission_transitions", "select"): [
                    {
                        "id": "tr-1",
                        "from_state": "SUBMITTED",
                        "to_state": "UNDER_REVIEW",
                        "actor_id": ADMIN_USER["id"],
                        "reason": None,
                        "transitioned_at": "2026-07-01T00:05:00+00:00",
                    }
                ],
            }
        )

    def test_detail_returns_evidence_and_leaks_no_protected_logic(self):
        db = self._detail_db()
        _as_admin()
        try:
            with patch(
                "backend_app.routers.library._build_service_client", return_value=db
            ):
                resp = client.get(f"{ADMIN_PREFIX}/{NAMED_SUBMISSION_ID}")
        finally:
            _clear_overrides()

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["submission"]["id"] == NAMED_SUBMISSION_ID
        assert body["evidence"][0]["id"] == "ev-1"

        # The detail must never read a source logic table.
        touched = {c.table_name for c in db.calls}
        assert "strategy_backtests" not in touched
        assert "strategy_versions" not in touched
        assert "strategies" not in touched

        # Requirement 5.8 / 6.8: the response carries no Protected_Logic token.
        listing_projection.assert_contains_no_protected_logic(body, PROTECTED_TOKENS)


# ══════════════════════════════════════════════════════════════════════════
# get_admin_user 403 BEFORE any read; body reveals no existence (5.1, 5.7)
# ══════════════════════════════════════════════════════════════════════════


class TestNonAdminForbiddenBeforeRead:
    def test_list_non_admin_is_403_and_no_read_happens(self):
        db = RecordingDB(
            script={("marketplace_submissions", "select"): [_list_row("x", "2026-07-01T00:00:00+00:00")]}
        )
        _as_non_admin()
        try:
            with patch(
                "backend_app.routers.library._build_service_client", return_value=db
            ):
                resp = client.get(ADMIN_PREFIX)
        finally:
            _clear_overrides()

        assert resp.status_code == 403, resp.text
        # get_admin_user refused before the body ran: the db recorded zero calls.
        assert db.calls == [], (
            "a non-admin must be refused by get_admin_user BEFORE any read; "
            f"the db recorded {db.ops()}"
        )

    def test_detail_non_admin_is_403_body_does_not_reveal_existence(self):
        # The db is scripted to "have" the named Submission and to NOT have a second one.
        # A non-admin's 403 must be the same for both — the response is not an oracle for
        # whether the Submission exists (Requirements 5.1, 5.7).
        existing_id = NAMED_SUBMISSION_ID
        missing_id = "44444444-4444-4444-4444-444444444444"
        db = RecordingDB(
            script={
                ("marketplace_submissions", "select"): lambda q: (
                    [{"id": existing_id, "submission_state": "UNDER_REVIEW"}]
                    if (existing_id in [v for _, v in q.filters])
                    else []
                )
            }
        )
        _as_non_admin()
        try:
            with patch(
                "backend_app.routers.library._build_service_client", return_value=db
            ):
                resp_existing = client.get(f"{ADMIN_PREFIX}/{existing_id}")
                resp_missing = client.get(f"{ADMIN_PREFIX}/{missing_id}")
        finally:
            _clear_overrides()

        assert resp_existing.status_code == 403, resp_existing.text
        assert resp_missing.status_code == 403, resp_missing.text

        # No read was attempted for either (the 403 comes from get_admin_user, before
        # admin_detail runs): the db recorded zero calls.
        assert db.calls == [], f"expected no read; the db recorded {db.ops()}"

        # The 403 body carries no per-Submission state and is identical apart from the
        # id the client itself put in the URL — so it cannot be used as an existence oracle.
        def _strip_request_specifics(payload: dict, sub_id: str) -> Any:
            # Drop the per-request timestamp; blank the client-supplied id out of the path.
            stripped = {k: v for k, v in payload.items() if k != "timestamp"}
            return _redact_ids(stripped, {sub_id})

        existing_body = _strip_request_specifics(resp_existing.json(), existing_id)
        missing_body = _strip_request_specifics(resp_missing.json(), missing_id)
        assert existing_body == missing_body, (
            "the 403 body differs for an existing vs a non-existing Submission, so it "
            f"reveals existence: {resp_existing.text!r} vs {resp_missing.text!r}"
        )

        # And it names no Submission_State value.
        lowered = resp_existing.text.lower()
        for state_word in ("under_review", "submission_state", "submitted", "published"):
            assert state_word not in lowered, (
                f"the 403 body leaks a state signal: {state_word!r} in {resp_existing.text!r}"
            )


# ══════════════════════════════════════════════════════════════════════════
# Distinct codes: unknown Submission vs rejected transition (5.9)
# ══════════════════════════════════════════════════════════════════════════


class TestDistinctErrorCodes:
    def test_unknown_submission_is_not_found_404(self):
        # apply_admin_action's FOR-UPDATE read returns no row => NOT_FOUND.
        db = RecordingDB(script={("marketplace_submissions", "select"): []})
        _as_admin()
        try:
            with patch(
                "backend_app.routers.library._build_service_client", return_value=db
            ):
                resp = client.post(f"{ADMIN_PREFIX}/{NAMED_SUBMISSION_ID}/approve")
        finally:
            _clear_overrides()

        assert resp.status_code == 404, resp.text
        assert resp.json()["error"]["code"] == MARKETPLACE_SUBMISSION_NOT_FOUND

    def test_rejected_transition_is_transition_rejected_409(self):
        # publish from DRAFT is an illegal edge => TRANSITION_REJECTED.
        db = RecordingDB(
            script={
                ("marketplace_submissions", "select"): [
                    {
                        "id": NAMED_SUBMISSION_ID,
                        "listing_id": "lst-1",
                        "source_strategy_id": "strat-1",
                        "owner_id": "owner-1",
                        "version_id": "ver-1",
                        "submission_state": "DRAFT",
                    }
                ]
            }
        )
        _as_admin()
        try:
            with patch(
                "backend_app.routers.library._build_service_client", return_value=db
            ):
                resp = client.post(f"{ADMIN_PREFIX}/{NAMED_SUBMISSION_ID}/publish")
        finally:
            _clear_overrides()

        assert resp.status_code == 409, resp.text
        assert (
            resp.json()["error"]["code"] == MARKETPLACE_SUBMISSION_TRANSITION_REJECTED
        )

    def test_the_two_codes_are_distinct(self):
        assert MARKETPLACE_SUBMISSION_NOT_FOUND != MARKETPLACE_SUBMISSION_TRANSITION_REJECTED


# ══════════════════════════════════════════════════════════════════════════
# A failed audit write rolls the state change back (5.11)
# ══════════════════════════════════════════════════════════════════════════


class _AuditFake:
    """A minimal recording Supabase double for apply_admin_action, tailored to the
    audit-rollback path: it scripts the FOR-UPDATE read and echoes the UPDATE's target
    state back, and records every call so the compensating revert is observable."""

    def __init__(self, current_state: str):
        self.current_state = current_state
        self.calls: List[Any] = []
        self._script = {
            ("marketplace_submissions", "select"): [
                {
                    "id": "sub-1",
                    "listing_id": "lst-1",
                    "source_strategy_id": "strat-1",
                    "owner_id": "owner-1",
                    "version_id": "ver-1",
                    "submission_state": current_state,
                }
            ],
        }

    def table(self, name):
        return _AuditQuery(name, self)


class _AuditQuery:
    def __init__(self, table, db):
        self.table_name = table
        self.db = db
        self.op = ""
        self.payload: Any = None
        self.filters: List[tuple] = []

    def select(self, *a, **k):
        self.op = "select"
        return self

    def insert(self, payload):
        self.op = "insert"
        self.payload = payload
        return self

    def update(self, payload):
        self.op = "update"
        self.payload = payload
        return self

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def order(self, *a, **k):
        return self

    def execute(self):
        self.db.calls.append(self)
        if self.op == "select":
            return _Resp(self.db._script.get((self.table_name, "select"), []))
        if self.op == "update":
            # Echo the target state back so _write_transition sees a successful UPDATE.
            return _Resp(
                [
                    {
                        "id": "sub-1",
                        "submission_state": self.payload.get("submission_state"),
                        "source_strategy_id": "strat-1",
                        "version_id": "ver-1",
                        "listing_id": "lst-1",
                    }
                ]
            )
        return _Resp([])


class TestAuditFailureRollsBack:
    def test_failed_audit_write_reverts_state_and_raises_not_recorded(self, monkeypatch):
        fake = _AuditFake("UNDER_REVIEW")

        async def _boom(*a, **k):
            raise RuntimeError("audit sink down")

        monkeypatch.setattr(svc, "_record_admin_action", _boom)

        with pytest.raises(SubmissionServiceError) as ei:
            asyncio.run(
                apply_admin_action(
                    {"id": "admin-1"},
                    "sub-1",
                    SubmissionAction.APPROVE,  # UNDER_REVIEW -> APPROVED, one edge
                    supabase=fake,
                )
            )

        assert ei.value.code == MARKETPLACE_ACTION_NOT_RECORDED

        # The compensation reverted submission_state back to the prior UNDER_REVIEW.
        revert = [
            c
            for c in fake.calls
            if c.table_name == "marketplace_submissions"
            and c.op == "update"
            and c.payload.get("submission_state") == "UNDER_REVIEW"
        ]
        assert revert, "a failed audit write must revert the state change (Requirement 5.11)"
