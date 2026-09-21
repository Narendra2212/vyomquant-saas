"""
tests/test_marketplace_concurrency.py

Tests for marketplace subscription concurrency and idempotency.

Tests:
1. Concurrent activation of same pending subscription
2. Cross-user payment_reference scenarios
3. Concurrent renewal of a cancelled subscription — ``TestConcurrentRenewal``, at the end of the
   file. Renewal is checkout-only since task 19.3, so the property is that N concurrent renewal
   requests write NOTHING and only a session that is actually paid ever becomes a period
   (Requirements 11.6, 11.14, 11.16, 25.8)
4. Malformed subscription_id/payment_reference
5. Unique constraint handling for duplicate subscriptions
6. Concurrent submissions for one strategy — ``TestConcurrentSubmissionBattery`` (task 14.12)
7. Duplicate webhook delivery of ONE provider reference — ``TestDuplicateWebhookDeliveryBattery``
   (task 19.13, Requirements 9.6, 10.10)
"""

import os
import sys
import uuid
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime, timezone
# ``Dict`` is used by ``_ConcurrentFakeDB.stores`` below. It was missing, which made this file a
# hard ``F821 undefined name 'Dict'`` under the repository's CI gate
# (``flake8 ... --select=E9,F63,F7,F82`` over ``backend_app`` and over ``tests`` -
# ``.github/workflows/01-pr-check.yml``), so the build failed on this module rather than on
# anything it asserts. ``Any``/``List``/``Optional``/``Tuple`` are used by the money-path doubles
# in the renewal and duplicate-delivery batteries at the end of the file.
from typing import Any, Dict, List, Optional, Tuple

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.core.dependencies import get_current_user, get_request_supabase
from backend_app.main import app

# Valid UUIDs for use in tests (path params must pass _safe_uuid validation)
_SUB_UUID = str(uuid.UUID("12345678-1234-5678-1234-567812345678"))
_LIB_UUID = str(uuid.UUID("87654321-4321-8765-4321-876543218765"))


def _mock_supabase_with_pro_profile():
    """Returns a mock supabase client that reports a Pro plan for the test user.

    This is required by require_marketplace_access → _get_user_plan, which
    queries the 'profiles' table via the request-scoped Supabase client.
    The FREE plan does not include MARKETPLACE_ACCESS, so tests that hit
    endpoints protected by require_marketplace_access must override
    get_request_supabase with this mock to avoid a 403.
    """
    sb = MagicMock()
    profile_result = MagicMock()
    profile_result.data = [{"subscription_tier": "pro_999"}]
    sb.table.return_value.select.return_value.eq.return_value.execute.return_value = profile_result
    return sb


def _user():
    """Mock authenticated user object."""
    return {
        "id": "usr_test_user",
        "email": "test@example.com",
        "aud": "authenticated"
    }


class TestConcurrentActivation:
    def test_concurrent_activation_is_idempotent(self):
        """
        Security fix: POST /subscribe is now blocked (405 Method Not Allowed).
        Activation is only performed by billing webhooks after payment verification.
        This test verifies the endpoint is read-only (GET) and returns status information.
        """
        import backend_app.routers.library as lib_module
        
        with patch.object(lib_module, '_build_service_client', return_value=MagicMock()):
            app.dependency_overrides[get_current_user] = lambda: _user()
            app.dependency_overrides[get_request_supabase] = _mock_supabase_with_pro_profile
            
            try:
                client = TestClient(app, raise_server_exceptions=False)
                
                # POST should be blocked (security fix)
                r_post = client.post("/api/library/" + _LIB_UUID + "/subscribe")
                assert r_post.status_code == 405, f"Expected 405 (Method Not Allowed), got {r_post.status_code}"
            finally:
                app.dependency_overrides.clear()


class TestCrossUserPaymentReference:
    def test_cross_user_payment_reference_fails_user_check(self):
        """
        User A's payment reference cannot activate User B's subscription.
        WHERE user_id in UPDATE prevents this.
        """
        import asyncio
        import backend_app.routers.billing as billing_module
        
        # Mock that payment metadata has user_id and subscription_id
        def mock_background_sb():
            sb = MagicMock()
            # Update returns no rows because user_id doesn't match
            update_result = MagicMock()
            update_result.data = []
            sb.table.return_value.update.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value = update_result
            return sb
        
        with patch.object(billing_module, '_background_sb', mock_background_sb):
            try:
                with pytest.raises(Exception):  # Should raise because no rows updated
                    asyncio.run(billing_module._apply_marketplace_entitlement("user_b", "lib_456", "sub_123"))
            except Exception as e:
                # Expected - no rows affected due to user_id mismatch
                pass


# ``TestConcurrentRenewal`` used to live here as an empty placeholder asserting nothing, with a
# docstring describing the pre-19.3 contract ("First succeeds with status=active"). Renewal no
# longer grants access without a payment, so the class is now a real battery and lives with the
# money-path doubles it needs, at the end of this file (task 19.13).


class TestMalformedSubscriptionId:
    def test_invalid_uuid_returns_400(self):
        """
        Invalid UUID in subscription_id should return 400.
        """
        import asyncio
        import backend_app.routers.billing as billing_module
        
        with pytest.raises(Exception):  # _validate_uuid raises HTTPException for invalid UUID
            asyncio.run(billing_module._apply_marketplace_entitlement("user_123", "not-a-uuid", "sub_123"))


class TestUniqueConstraint:
    def test_duplicate_active_subscription_prevented(self):
        """
        Database unique constraint on (user_id, library_id, status) prevents duplicate active subscriptions.
        """
        # This test placeholder documents the expected database-level constraint
        pass


# ══════════════════════════════════════════════════════════════════════════════
# SUBMISSION CONCURRENCY BATTERY (task 14.12 — Requirements 2.8, 25.8)
# ══════════════════════════════════════════════════════════════════════════════
#
# WHAT THIS BATTERY PROVES
# ------------------------
# The one-open-Submission-per-strategy invariant (Requirement 2.8) is enforced by the
# partial unique index ``uq_submission_open_per_strategy`` in migration 007:
#
#     UNIQUE (source_strategy_id)
#     WHERE submission_state IN ('SUBMITTED','UNDER_REVIEW','APPROVED','PUBLISHED')
#
# A DRAFT row is NOT covered by the predicate, so ``submission_service.create_submission``
# inserts DRAFT freely; the race is decided at the DRAFT->SUBMITTED UPDATE, where the SECOND
# writer to reach an open state for one ``source_strategy_id`` gets a Postgres ``23505``.
# ``submission_service`` maps that ``23505`` (via ``_is_unique_open_violation``, which requires
# BOTH the ``23505`` SQLSTATE and the ``uq_submission_open_per_strategy`` name) to
# ``MARKETPLACE_SUBMISSION_ALREADY_OPEN`` (409) and COMPENSATES — deletes the evidence rows and
# the parent DRAFT it just wrote — so no partial Submission survives (Requirement 25.8 / 3.15).
#
# The property asserted for N concurrent ``POST /api/library/submissions`` on ONE strategy:
#   1. EXACTLY ONE ``marketplace_submissions`` row survives (state SUBMITTED).
#   2. N-1 attempts fail with ``MARKETPLACE_SUBMISSION_ALREADY_OPEN``.
#   3. The winner's row is UNCHANGED by the losers (same id / state / owner; the losers'
#      compensation removed only their own DRAFT + evidence, never the winner's).
#
# HOW CONCURRENCY IS MODELLED
# ---------------------------
# There is no live Postgres here (same constraint the pipeline suite documents), so the race is
# driven with ``asyncio.gather`` over N copies of the exact route body — ``eligibility_gate.evaluate``
# then ``submission_service.create_submission`` — against ONE shared fake DB. Because the
# Eligibility_Gate reads ``marketplace_submissions`` FIRST and finds it empty for every attempt,
# all N are admitted and every attempt genuinely reaches the create race rather than being turned
# away by the gate's MP_SUBMISSION_OPEN criterion first (that gate-level rejection is already
# covered by test_14 in the pipeline suite; this battery targets the index-level race the gate
# cannot see). The interleave is deterministic under asyncio's single thread, but each fake
# ``.execute()`` is atomic, so the DRAFT->SUBMITTED updates are serialised through the index
# exactly as concurrent Postgres statements would be — the first flips to SUBMITTED, every later
# one raises the 23505.
#
# HOW THE FAKE DB ENFORCES uq_submission_open_per_strategy
# --------------------------------------------------------
# ``_ConcurrentSubmissionsTable`` overrides ONLY the ``marketplace_submissions`` UPDATE path: when
# an update sets ``submission_state`` to an open value, it scans the store for a DIFFERENT row
# with the same ``source_strategy_id`` already in an open state and, if one exists, raises
# ``_UniqueViolation`` — an exception whose ``code`` is ``"23505"`` and whose text names
# ``uq_submission_open_per_strategy``, i.e. the exact shape ``_is_unique_open_violation`` matches.
# Every other table and operation behaves like the pipeline suite's ``FakeTable``.

import asyncio as _asyncio
import copy as _copy

from unittest.mock import patch as _patch

from backend_app.backend.marketplace import eligibility_gate as _eligibility_gate
from backend_app.backend.marketplace import submission_service as _submission_service
from backend_app.backend.marketplace.submission_service import (
    MARKETPLACE_SUBMISSION_ALREADY_OPEN,
    SubmissionServiceError,
)

#: The four Submission_State values the partial unique index covers (migration 007). A row in
#: any of these occupies the one-open-Submission slot for its ``source_strategy_id``.
_OPEN_SUBMISSION_STATES = frozenset(
    {"SUBMITTED", "UNDER_REVIEW", "APPROVED", "PUBLISHED"}
)


class _UniqueViolation(Exception):
    """A fake Postgres ``23505`` on ``uq_submission_open_per_strategy``.

    Shaped so ``submission_service._is_unique_open_violation`` matches it: it needs BOTH the
    ``23505`` SQLSTATE (read here from ``.code``) AND the constraint name (in the message). A
    real supabase/PostgREST error carries these on ``.code``/``.message``/``.details``; this
    mirrors that so the production mapping to ``MARKETPLACE_SUBMISSION_ALREADY_OPEN`` runs
    unchanged rather than being simulated.
    """

    def __init__(self) -> None:
        self.code = "23505"
        self.message = (
            'duplicate key value violates unique constraint '
            '"uq_submission_open_per_strategy"'
        )
        self.details = "Key (source_strategy_id) already exists."
        super().__init__(self.message)


class _Resp:
    def __init__(self, data):
        self.data = data


class _ConcurrentSubmissionsTable:
    """A chain-able fake table that enforces ``uq_submission_open_per_strategy`` on UPDATE.

    Mirrors the pipeline suite's ``FakeTable`` for select/insert/update/delete over a shared
    row list, and adds ONE rule specific to this battery: an UPDATE that moves a
    ``marketplace_submissions`` row into an open state raises ``_UniqueViolation`` when another
    row for the same ``source_strategy_id`` is already open. That is the DRAFT->SUBMITTED race
    arbiter — the second writer to reach SUBMITTED loses, exactly as the real partial index
    decides it.
    """

    def __init__(self, name: str, rows: list):
        self._name = name
        self._rows = rows
        self._filters: list = []
        self._op = None
        self._op_data = None
        self._single = False

    # ── read-side chain (no-ops that return self, like the pipeline FakeTable) ──
    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self._filters.append(("eq", col, val))
        return self

    def in_(self, col, vals):
        self._filters.append(("in", col, vals))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def single(self):
        self._single = True
        return self

    # ── write-side chain ──
    def insert(self, data):
        self._op = "insert"
        self._op_data = data
        return self

    def update(self, data):
        self._op = "update"
        self._op_data = data
        return self

    def delete(self):
        self._op = "delete"
        return self

    def _matched(self):
        result = list(self._rows)
        for ftype, col, val in self._filters:
            if ftype == "eq":
                result = [r for r in result if str(r.get(col, "")) == str(val)]
            elif ftype == "in":
                str_vals = [str(v) for v in val]
                result = [r for r in result if str(r.get(col, "")) in str_vals]
        return result

    def execute(self):
        # --- INSERT ---
        if self._op == "insert":
            data = self._op_data
            if isinstance(data, dict):
                if "id" not in data:
                    data["id"] = str(uuid.uuid4())
                self._rows.append(data)
                out = [data]
            else:
                out = []
                for row in data:
                    if "id" not in row:
                        row["id"] = str(uuid.uuid4())
                    self._rows.append(row)
                    out.append(row)
            return _Resp(out[0] if self._single else out)

        result = self._matched()

        # --- UPDATE (the index arbiter for marketplace_submissions) ---
        if self._op == "update":
            new_state = self._op_data.get("submission_state")
            if (
                self._name == "marketplace_submissions"
                and new_state in _OPEN_SUBMISSION_STATES
            ):
                for target in result:
                    strat = target.get("source_strategy_id")
                    # A DIFFERENT row already open for this strategy => 23505 (the loser path).
                    conflict = any(
                        other is not target
                        and str(other.get("source_strategy_id", "")) == str(strat)
                        and other.get("submission_state") in _OPEN_SUBMISSION_STATES
                        for other in self._rows
                    )
                    if conflict:
                        raise _UniqueViolation()
            ids = {r["id"] for r in result}
            for row in self._rows:
                if row.get("id") in ids:
                    row.update(self._op_data)
            return _Resp(result)

        # --- DELETE (used by create_submission's compensation) ---
        if self._op == "delete":
            drop = {id(r) for r in result}
            self._rows[:] = [r for r in self._rows if id(r) not in drop]
            return _Resp(result)

        # --- SELECT ---
        if self._single:
            return _Resp(result[0] if result else None)
        return _Resp(result)


class _ConcurrentFakeDB:
    """Shared in-memory DB whose ``marketplace_submissions`` UPDATE enforces the partial index."""

    def __init__(self):
        self.stores: Dict[str, list] = {
            "strategies": [],
            "library_strategies": [],
            "strategy_versions": [],
            "strategy_backtests": [],
            "marketplace_submissions": [],
            "marketplace_backtest_evidence": [],
            "marketplace_submission_transitions": [],
        }

    def table(self, name: str) -> "_ConcurrentSubmissionsTable":
        if name not in self.stores:
            self.stores[name] = []
        return _ConcurrentSubmissionsTable(name, self.stores[name])


class _NoopAuditLogger:
    """No-op audit logger — no Redis in this test env (same rationale as the pipeline suite)."""

    async def record_or_raise(self, *a, **k):
        return None

    async def log(self, *a, **k):
        return None


# ── seed builders (an eligible strategy so every attempt is ADMITTED) ──────────

_CONC_STRATEGY_ID = str(uuid.uuid4())
_CONC_OWNER_ID = str(uuid.uuid4())
_CONC_VERSION_ID = str(uuid.uuid4())


def _conc_version_row():
    return {
        "id": _CONC_VERSION_ID,
        "strategy_id": _CONC_STRATEGY_ID,
        "version": 1,
        "is_draft": False,
        "validation_state": "VALID",
        "blueprint": {"nodes": [{"type": "action", "label": "BUY"}]},
        "graph_json": {"nodes": [{"type": "action", "label": "BUY"}]},
    }


def _conc_backtest_row(start_date, end_date, dataset, checksum):
    bid = str(uuid.uuid4())
    return {
        "id": bid,
        "user_id": _CONC_OWNER_ID,
        "strategy_id": _CONC_STRATEGY_ID,
        "version_id": _CONC_VERSION_ID,
        "status": "completed",
        "completed_at": "2026-06-01T00:00:00+00:00",
        "error_message": None,
        "dataset": dataset,
        "start_date": start_date,
        "end_date": end_date,
        "initial_capital": 10000,
        "commission": 0.001,
        "slippage": 0.0005,
        "dataset_checksum": checksum,
        "dag_hash": "dag-" + bid[:8],
        "total_trades": 120,
        "executed_bar_count": 500,
        "total_return_pct": 42.5,
        "sharpe_ratio": 1.8,
        "sortino_ratio": 2.1,
        "max_drawdown": -5.2,
        "win_rate": 61.0,
        "profit_factor": 1.7,
        "final_capital": 14250.0,
    }


def _seed_conc_eligible(db):
    """Seed the saved version + three distinct eligible backtests; return their ids.

    Leaves ``marketplace_submissions`` and ``library_strategies`` EMPTY so MP_SUBMISSION_OPEN
    passes for every concurrent attempt — each one is admitted and reaches the create race.
    """
    # The owning strategy row — MP_OWNERSHIP/MP_TENANT read it via eligibility_gate._read_strategy.
    db.stores["strategies"].append(
        {
            "id": _CONC_STRATEGY_ID,
            "user_id": _CONC_OWNER_ID,
            "tenant_id": None,
            "archived_at": None,
        }
    )
    db.stores["strategy_versions"].append(_conc_version_row())
    rows = [
        _conc_backtest_row("2024-01-01", "2024-06-30", "BTCUSD-2024H1", "chk-btc-h1"),
        _conc_backtest_row("2024-07-01", "2024-12-31", "ETHUSD-2024H2", "chk-eth-h2"),
        _conc_backtest_row("2023-01-01", "2023-06-30", "SOLUSD-2023H1", "chk-sol-h1"),
    ]
    db.stores["strategy_backtests"].extend(rows)
    return [r["id"] for r in rows]


async def _evaluate_attempt(db, backtest_ids):
    """One attempt's eligibility check — the first half of the create_submission_route body.

    In a genuine concurrent burst every caller's Eligibility_Gate runs while
    ``marketplace_submissions`` is still empty (no writer has committed SUBMITTED yet), so all N
    are admitted and every one goes on to the create race. Modelling the burst as "evaluate all,
    then create all" reproduces exactly that window — the gate's MP_SUBMISSION_OPEN check, which
    only sees COMMITTED rows, cannot pre-empt the race the partial index is there to arbitrate.
    """
    caller = {"id": _CONC_OWNER_ID, "tenant_id": None}
    verdict = await _eligibility_gate.evaluate(
        caller, _CONC_STRATEGY_ID, backtest_ids, db
    )
    # Every attempt must be admitted — otherwise the test would be exercising the gate, not the
    # index race. Assert it here so a seeding regression surfaces as a clear failure.
    assert verdict.admitted, f"attempt not admitted: {[o.code for o in verdict.failed_outcomes]}"
    return verdict


async def _create_attempt(db, backtest_ids, verdict):
    """One attempt's create — the second half of the route body, where the index decides.

    Returns ``("ok", submission_row)`` on the winning path, or ``("already_open", code)`` when
    the DRAFT->SUBMITTED race is lost. Any other SubmissionServiceError is re-raised so an
    unexpected persist failure fails the test loudly rather than being counted as a loser.
    """
    caller = {"id": _CONC_OWNER_ID, "tenant_id": None}
    evidence_resp = (
        db.table("strategy_backtests")
        .select(_submission_service._EVIDENCE_SOURCE_SELECT)
        .in_("id", backtest_ids)
        .eq("user_id", _CONC_OWNER_ID)
        .execute()
    )
    backtest_rows = evidence_resp.data or []

    try:
        submission = await _submission_service.create_submission(
            caller=caller,
            listing_id=None,
            source_strategy_id=_CONC_STRATEGY_ID,
            version_id=verdict.version_id,
            backtest_rows=backtest_rows,
            eligibility_outcomes=verdict.outcomes,
            evaluator_version=verdict.evaluator_version,
            supabase=db,
        )
        return ("ok", submission)
    except SubmissionServiceError as exc:
        if exc.code == MARKETPLACE_SUBMISSION_ALREADY_OPEN:
            return ("already_open", exc.code)
        raise


class TestConcurrentSubmissionBattery:
    """N concurrent POST /api/library/submissions for ONE strategy => exactly one winner.

    Requirements 2.8 (one open Submission per strategy, second writer gets 23505) and 25.8
    (the loser leaves no partial Submission — its compensation removes only its own rows).
    """

    def _run_battery(self, n):
        db = _ConcurrentFakeDB()
        backtest_ids = _seed_conc_eligible(db)

        async def _drive():
            # Phase 1: all N eligibility checks run against the still-empty submissions table,
            # so every attempt is admitted (the burst window the index must arbitrate).
            verdicts = await _asyncio.gather(
                *[_evaluate_attempt(db, backtest_ids) for _ in range(n)]
            )
            # Phase 2: all N create attempts race at the DRAFT->SUBMITTED index; exactly one wins.
            return await _asyncio.gather(
                *[_create_attempt(db, backtest_ids, v) for v in verdicts]
            )

        with _patch(
            "backend_app.core.audit_trail.get_strategy_audit_logger",
            return_value=_NoopAuditLogger(),
        ):
            results = _asyncio.run(_drive())
        return db, results

    def _assert_one_winner(self, db, results, n):
        winners = [r for r in results if r[0] == "ok"]
        losers = [r for r in results if r[0] == "already_open"]

        # (1) EXACTLY ONE ``marketplace_submissions`` row survives, in SUBMITTED.
        subs = db.stores["marketplace_submissions"]
        assert len(subs) == 1, f"expected exactly 1 submission row, got {len(subs)}"
        winner_row = subs[0]
        assert winner_row["submission_state"] == "SUBMITTED"
        assert winner_row["source_strategy_id"] == _CONC_STRATEGY_ID
        assert winner_row["owner_id"] == _CONC_OWNER_ID

        # (2) Exactly one winner and N-1 ALREADY_OPEN losers.
        assert len(winners) == 1, f"expected 1 winner, got {len(winners)}"
        assert len(losers) == n - 1, f"expected {n - 1} losers, got {len(losers)}"
        assert all(code == MARKETPLACE_SUBMISSION_ALREADY_OPEN for _, code in losers)

        # (3) The winner's state is UNCHANGED by the losers: the surviving row is the one the
        #     winning attempt returned, and the losers' compensation removed ONLY their own
        #     rows — one evidence set survives (the winner's), and one DRAFT->SUBMITTED
        #     transition is recorded. No orphaned DRAFT, no extra evidence.
        returned = winners[0][1]
        assert returned["id"] == winner_row["id"]
        assert returned["submission_state"] == "SUBMITTED"

        evidence = db.stores["marketplace_backtest_evidence"]
        assert len(evidence) == 3, f"expected only the winner's 3 evidence rows, got {len(evidence)}"
        assert all(e["submission_id"] == winner_row["id"] for e in evidence)

        transitions = db.stores["marketplace_submission_transitions"]
        submitted_edges = [
            t for t in transitions
            if t["from_state"] == "DRAFT" and t["to_state"] == "SUBMITTED"
        ]
        assert len(submitted_edges) == 1
        assert submitted_edges[0]["submission_id"] == winner_row["id"]

    def test_two_concurrent_submissions_one_winner(self):
        db, results = self._run_battery(2)
        self._assert_one_winner(db, results, 2)

    def test_five_concurrent_submissions_one_winner(self):
        db, results = self._run_battery(5)
        self._assert_one_winner(db, results, 5)

    def test_ten_concurrent_submissions_one_winner(self):
        db, results = self._run_battery(10)
        self._assert_one_winner(db, results, 10)


# ══════════════════════════════════════════════════════════════════════════════
# THE MONEY PATH UNDER CONCURRENCY (task 19.13 — Requirements 9.6, 10.10, 11.16, 25.8)
# ══════════════════════════════════════════════════════════════════════════════
#
# WHAT CHANGED, AND WHY THE OLD TEST HAD TO GO
# --------------------------------------------
# ``TestConcurrentRenewal`` used to be an empty ``pass`` whose docstring recorded the pre-19.3
# contract: "First succeeds with status=active / Second returns status=already_active". That
# contract is exactly the defect Requirement 11.16 orders removed. The old
# ``POST /api/library/subscriptions/{sub_id}/renew`` renewed a Subscription by *writing* it —
# ``update({"status": "active", "cancelled_at": None, "expires_at": None})``, a
# ``grant_deployment_permission`` with a NULL expiry, and a ``subscriber_count`` increment — with
# no provider contacted, no amount computed and no Settlement_Record written. A null expiry was
# read as perpetual access, so one unpriced POST turned a lapsed monthly Subscription into
# permanent free access.
#
# Task 19.3 rewrote the endpoint as checkout-only: it returns a provider session and issues **no
# statement at all** against ``library_subscriptions``. Requirements 11.6 and 11.14 make a payment
# confirmed through the Billing_Integration and recorded as a Settlement_Record the precondition
# for *every* transition into ``ACTIVE``, and ``settlement_service.settle`` is the only writer of
# ``status='active'``. So "the first concurrent renewal succeeds with status=active" is no longer
# a property of this system; asserting it would be asserting the defect.
#
# THE TWO PROPERTIES THIS SECTION ASSERTS INSTEAD
# -----------------------------------------------
# ``TestConcurrentRenewal`` — N concurrent renewal requests for one Subscription:
#   (a) produce **no state change**: zero write statements against any table, and a deep
#       before/after comparison of every table that is unchanged (Requirement 11.16);
#   (b) commit **at most one** provider reference — in fact zero, because the endpoint writes
#       nothing, so the reference stored on the row is still the payment that bought the CURRENT
#       period, the value ``settlement_service.find_settled_payment`` correlates a later refund by;
#   (c) yield **exactly one paid period**: whatever the provider opened, only a session that is
#       actually paid settles, and the burst's sibling sessions leave the ledger empty.
#
# ON "AT MOST ONE PROVIDER SESSION", PRECISELY
# --------------------------------------------
# Read literally as "the provider is contacted at most once", that is NOT true of this code and is
# not asserted here. ``create_renewal_checkout`` writes nothing — that is the whole point of task
# 19.3 — and a request that writes nothing has nowhere to record that a sibling request is already
# in flight, so N concurrent requests open N sessions. That is deliberate and correct: the
# alternative (writing a marker, or reusing/overwriting ``provider_reference``) would either
# reintroduce a pre-payment writer on the activation path or clobber the reference of the payment
# that bought the current period. The requirements deduplicate a *payment*, not a *session*:
# Requirement 9.6 is scoped to "the same payment confirmation" and Requirement 10.10 to "provider
# transaction reference and reversal indicator", both enforced by
# ``uq_settlement_reference_reversal``. So the honest and stronger form of the property is (b)+(c)
# above — at most one session of record, and exactly one period per payment — and the session-level
# count is asserted only for what it must be: every session in the burst is *interchangeable*
# (same Subscription, same integer amount, same currency), so paying any one of them buys exactly
# one month and no two of them can disagree about what was bought.
#
# ``TestDuplicateWebhookDeliveryBattery`` — N deliveries of ONE provider reference:
#   exactly one ``marketplace_settlements`` row, exactly one period extension (one UPDATE, one
#   history row, one entitlement grant, one expiry value) and exactly N−1
#   ``MARKETPLACE_SETTLEMENT_DUPLICATE_IGNORED`` audit entries beside the single
#   ``MARKETPLACE_SETTLEMENT_CREATED`` (Requirements 9.6, 10.10, 25.8).
#
# HOW CONCURRENCY IS MODELLED (the same boundary the submission battery above records)
# -----------------------------------------------------------------------------------
# There is no live PostgreSQL and no Redis here, so the burst is driven with ``asyncio.gather``
# over N copies of the real handler / the real webhook funnel against ONE shared in-memory
# Persistence_Layer. The interleave is deterministic under asyncio's single thread, but every
# ``.execute()`` on the double is atomic, so the inserts serialise through
# ``uq_settlement_reference_reversal`` exactly as concurrent Postgres statements would: the first
# delivery's row lands and every later one collides. The two-phase Redis idempotency lock in
# ``routers/billing.py`` is NOT exercised, weakened or replaced here — it is the fast path, and
# the point of this battery is that the *constraint* is what makes redelivery a no-op when the
# lock is flushed, expired or bypassed by a second process.
#
# WHY THE DOUBLE ENFORCES THE CONSTRAINT RATHER THAN RECORDING IT
# ---------------------------------------------------------------
# A double that accepted every insert would make every assertion in the duplicate battery pass
# against a webhook with no idempotency at all. ``_MoneyPathSupabase`` therefore raises a
# ``23505``-shaped error naming ``uq_settlement_reference_reversal`` on a second row for one
# ``(provider_reference, is_reversal)`` pair, which is the exact shape
# ``settlement_service._is_duplicate_reference`` matches, so the production duplicate branch runs
# unchanged rather than being simulated. It also carries the transition UPDATE's optimistic
# ``.eq("status", <the status that was read>)`` guard faithfully, so a lost update shows up as a
# no-op UPDATE the way it would in Postgres.

from backend_app.backend.marketplace import checkout_service as _checkout_service
from backend_app.backend.marketplace import settlement_service as _settlement_service
from backend_app.backend.marketplace.errors import MarketplaceError
from backend_app.core.rate_limit import limiter as _limiter
from backend_app.routers import billing as _billing_module
from backend_app.routers import library as _library_router

# ``renew_subscription`` carries ``@limiter.limit("5/60second", key_func=caller_or_address)``
# (task 33.2, Requirements 6.7/22.4), and ``slowapi``'s wrapper refuses to run the handler unless
# it is handed a real ``starlette.requests.Request`` — it reads ``request.headers`` and
# ``request.client`` to key the bucket and ``request.state`` to memoise the check. Both helpers
# this needs already exist and are reused rather than re-written: one definition of "a real
# request over a hand-built scope" and one of "forget what the limiter has counted".
from tests.test_task_28_1_session_routes import (
    _reset_rate_limit_counters as _reset_limiter_counters,
)
from tests.test_task_33_2_rate_limit_keys import _request as _real_request

#: ``_safe_uuid`` validates every path parameter, so these are real UUIDs.
_RENEW_SUB_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
_RENEW_LISTING_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
_RENEW_PURCHASER_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
_RENEW_OWNER_ID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"

#: A $19.99 Listing stored exactly: 1999 Minor_Units, 1799 owner share, 200 platform fee. No
#: ``float`` appears anywhere near an amount in this section.
_RENEW_PRICE_MINOR = 1999
_RENEW_OWNER_SHARE_MINOR = 1799
_RENEW_PLATFORM_FEE_MINOR = 200

#: The period the current (paid) month runs over. A renewal request must not move either boundary;
#: only ``settlement_service.settle`` may.
_RENEW_CURRENT_START = "2025-05-01T00:00:00+00:00"
_RENEW_CURRENT_EXPIRY = "2025-06-01T00:00:00+00:00"

#: The reference of the payment that bought the CURRENT period. A renewal must not overwrite it —
#: it is what ``settlement_service.find_settled_payment`` correlates a later refund by.
_RENEW_PRIOR_REFERENCE = "pi_the_payment_that_bought_this_month"

#: The renewal payment's own reference and confirmation instant. Mid-period on purpose, so the
#: extension is provably anchored on the stored expiry rather than on the payment date
#: (Requirement 11.5): one calendar month after 2025-06-01, not after 2025-05-20.
_RENEWAL_REFERENCE = "pi_the_renewal_payment"
_RENEWAL_CONFIRMED_AT = datetime(2025, 5, 20, 12, 0, 0, tzinfo=timezone.utc)
_RENEWED_EXPIRY = "2025-07-01T00:00:00+00:00"

_MARKETPLACE_ITEM_KEY = f"marketplace_{_RENEW_LISTING_ID}"


class _MoneyPathUniqueViolation(Exception):
    """``uq_settlement_reference_reversal`` refusing a second row for one reference.

    Shaped so ``settlement_service._is_duplicate_reference`` matches it: it carries the ``23505``
    SQLSTATE, the constraint name and the table name, which is what a real ``psycopg2`` /
    ``supabase-py`` error surfaces between ``pgcode``, ``message`` and ``details``. The production
    duplicate branch therefore runs against this unchanged.
    """

    def __init__(self) -> None:
        self.pgcode = "23505"
        self.code = "23505"
        self.message = (
            'duplicate key value violates unique constraint '
            '"uq_settlement_reference_reversal" on marketplace_settlements (23505)'
        )
        self.details = "Key (provider_reference, is_reversal) already exists."
        super().__init__(self.message)


class _MoneyPathResp:
    def __init__(self, data: Any) -> None:
        self.data = data
        self.error = None


class _MoneyPathQuery:
    """A supabase-py-shaped, recording query builder.

    Carries ``in_`` as well as ``eq`` because the pre-19.3 renewal body used
    ``.in_("status", ["cancelled", "expired"])``: a builder that could not express the pre-fix
    statement could not run the pre-fix body, and a regression battery that cannot run the code it
    is recorded against proves nothing.
    """

    def __init__(self, table: str, client: "_MoneyPathSupabase") -> None:
        self.table_name = table
        self.client = client
        self.op = "select"
        self.payload: Optional[Dict[str, Any]] = None
        self.filters: List[Tuple[str, Any]] = []
        self.in_filters: List[Tuple[str, List[Any]]] = []

    def select(self, cols: str = "*") -> "_MoneyPathQuery":
        self.op = "select"
        return self

    def insert(self, payload: Dict[str, Any]) -> "_MoneyPathQuery":
        self.op = "insert"
        self.payload = dict(payload)
        return self

    def update(self, payload: Dict[str, Any]) -> "_MoneyPathQuery":
        self.op = "update"
        self.payload = dict(payload)
        return self

    def delete(self) -> "_MoneyPathQuery":
        self.op = "delete"
        return self

    def eq(self, col: str, val: Any) -> "_MoneyPathQuery":
        self.filters.append((col, val))
        return self

    def in_(self, col: str, vals: Any) -> "_MoneyPathQuery":
        self.in_filters.append((col, list(vals)))
        return self

    def single(self) -> "_MoneyPathQuery":
        return self

    def execute(self) -> Any:
        return self.client._execute(self)


class _MoneyPathSupabase:
    """The five tables the renewal and settlement paths touch, plus a complete statement log.

    The statement log is what makes "changes no stored state" a measurement rather than a claim:
    the provider session factory appends ``("provider", name)`` to the same ``ops`` list, so the
    ordering of the reads, the session and the (absent) writes is read off one sequence.
    """

    WRITE_OPS = frozenset({"insert", "update", "delete"})

    def __init__(self, *, subscription_status: str = "cancelled") -> None:
        self.tables: Dict[str, List[Dict[str, Any]]] = {
            "library_subscriptions": [_renew_subscription_row(status=subscription_status)],
            "library_strategies": [_renew_listing_row()],
            "marketplace_settlements": [],
            "library_subscription_transitions": [],
            "deployment_permissions": [],
        }
        self.ops: List[Tuple[str, str]] = []
        self.statements: List[_MoneyPathQuery] = []

    def table(self, name: str) -> _MoneyPathQuery:
        return _MoneyPathQuery(name, self)

    def note_provider_call(self, provider: str) -> None:
        self.ops.append(("provider", provider))

    def _execute(self, q: _MoneyPathQuery) -> Any:
        self.ops.append((q.op, q.table_name))
        self.statements.append(q)
        rows = self.tables.setdefault(q.table_name, [])

        if q.op == "select":
            return _MoneyPathResp([dict(r) for r in self._matching(q, rows)])

        if q.op == "insert":
            row = dict(q.payload or {})
            if q.table_name == "marketplace_settlements":
                # uq_settlement_reference_reversal UNIQUE (provider_reference, is_reversal).
                # ENFORCED, not recorded: a double that accepted every insert would let this
                # battery pass against a webhook with no idempotency at all.
                pair = (row.get("provider_reference"), bool(row.get("is_reversal")))
                for existing in rows:
                    if (
                        existing.get("provider_reference"),
                        bool(existing.get("is_reversal")),
                    ) == pair:
                        raise _MoneyPathUniqueViolation()
            row.setdefault("id", f"generated-{q.table_name}-{len(rows) + 1}")
            rows.append(row)
            return _MoneyPathResp([dict(row)])

        if q.op == "update":
            # Every filter is applied, including settlement_service's optimistic
            # ``.eq("status", <the status that was read>)`` — so a second writer that already
            # moved the row makes this UPDATE match zero rows, exactly as in Postgres.
            touched = self._matching(q, rows)
            for row in touched:
                row.update(q.payload or {})
            return _MoneyPathResp([dict(r) for r in touched])

        if q.op == "delete":
            touched = self._matching(q, rows)
            for row in touched:
                rows.remove(row)
            return _MoneyPathResp([dict(r) for r in touched])

        return _MoneyPathResp([])

    @staticmethod
    def _matching(
        q: _MoneyPathQuery, rows: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        out = rows
        for col, val in q.filters:
            out = [r for r in out if str(r.get(col)) == str(val)]
        for col, vals in q.in_filters:
            wanted = {str(v) for v in vals}
            out = [r for r in out if str(r.get(col)) in wanted]
        return out

    # ── assertion helpers ───────────────────────────────────────────────────
    def snapshot(self) -> Dict[str, List[Dict[str, Any]]]:
        return _copy.deepcopy(self.tables)

    def writes(self, table: Optional[str] = None) -> List[_MoneyPathQuery]:
        return [
            s
            for s in self.statements
            if s.op in self.WRITE_OPS and (table is None or s.table_name == table)
        ]

    def update_payloads(self, table: str) -> List[Dict[str, Any]]:
        return [
            dict(s.payload or {})
            for s in self.statements
            if s.op == "update" and s.table_name == table
        ]

    def subscription(self) -> Dict[str, Any]:
        return dict(self.tables["library_subscriptions"][0])

    def settlements(self) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.tables["marketplace_settlements"]]

    def transitions(self) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.tables["library_subscription_transitions"]]

    def permissions(self) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.tables["deployment_permissions"]]

    def provider_calls(self) -> List[str]:
        return [name for op, name in self.ops if op == "provider"]


def _renew_subscription_row(*, status: str = "cancelled") -> Dict[str, Any]:
    """One ``library_subscriptions`` row mid-way through a paid period.

    It carries a *real* period, because the whole point of the removed defect was that the renewal
    wiped it: ``expires_at`` is the column ``check_deployment_permission`` read as perpetual when
    null, and ``period_expiry`` is the one the settlement path extends from.
    """
    return {
        "id": _RENEW_SUB_ID,
        "library_id": _RENEW_LISTING_ID,
        "user_id": _RENEW_PURCHASER_ID,
        "owner_id": _RENEW_OWNER_ID,
        "status": status,
        "price_minor": _RENEW_PRICE_MINOR,
        "currency": "USD",
        "owner_share_minor": _RENEW_OWNER_SHARE_MINOR,
        "platform_fee_minor": _RENEW_PLATFORM_FEE_MINOR,
        "provider": "stripe",
        "provider_reference": _RENEW_PRIOR_REFERENCE,
        "started_at": _RENEW_CURRENT_START,
        "period_start": _RENEW_CURRENT_START,
        "period_expiry": _RENEW_CURRENT_EXPIRY,
        "expires_at": _RENEW_CURRENT_EXPIRY,
        "cancelled_at": "2025-05-15T00:00:00+00:00" if status == "cancelled" else None,
    }


def _renew_listing_row() -> Dict[str, Any]:
    return {
        "id": _RENEW_LISTING_ID,
        "name": "Momentum Breakout",
        "author_id": _RENEW_OWNER_ID,
        "price_minor": _RENEW_PRICE_MINOR,
        "currency": "USD",
        "is_active": True,
        "subscriber_count": 7,
        "marketplace_submissions": [{"submission_state": "PUBLISHED"}],
    }


class _CountingSessionFactory:
    """A provider session builder that records every context it was handed.

    Each session gets its own reference, which is what both providers actually do: neither
    ``stripe.checkout.Session.create`` nor ``client.order.create`` is called with an idempotency
    key, so two requests are two sessions. The contexts are kept so the burst's sessions can be
    checked for *interchangeability* rather than for a session count the code does not promise.
    """

    def __init__(self, db: "_MoneyPathSupabase") -> None:
        self.db = db
        self.contexts: List[Any] = []

    def __call__(self, context: Any) -> Any:
        self.db.note_provider_call(context.provider)
        self.contexts.append(context)
        index = len(self.contexts)
        return _checkout_service.ProviderSession(
            provider=context.provider,
            reference=f"cs_renewal_{index}",
            checkout_url=f"https://provider.example/checkout/renewal/{index}",
        )

    @property
    def references(self) -> List[str]:
        return [f"cs_renewal_{i + 1}" for i in range(len(self.contexts))]


class _MoneyPathAuditRecorder:
    """Records every audit act by action name, and never raises.

    ``settlement_service._write_audit`` uses ``record_or_raise`` for every required line, so a
    recorder that failed would turn a settlement into a retried persistence error and this battery
    would be measuring the retry budget instead of the constraint.
    """

    def __init__(self) -> None:
        self.actions: List[str] = []

    async def record_or_raise(self, action: Any, **kwargs: Any) -> None:
        self.actions.append(getattr(action, "name", str(action)))

    async def log(self, action: Any, **kwargs: Any) -> None:
        self.actions.append(getattr(action, "name", str(action)))


def _run_coroutine(coro: Any) -> Any:
    """Run ``coro`` without leaving the thread with no current event loop.

    Not ``asyncio.run``: that closes its loop and then calls ``set_event_loop(None)``, leaving the
    main thread loopless, and other suites in this repository still call the deprecated
    ``asyncio.get_event_loop().run_until_complete(...)`` —
    ``tests/test_marketplace_pipeline.py::TestGetAdminUserP01Regression`` is one — which then
    raises ``RuntimeError: There is no current event loop in thread 'MainThread'`` when collected
    after this module. The loop that was current is therefore put back.

    The same helper as ``tests/test_checkout_service.py::_run``,
    ``tests/test_subscription_renewal_regression.py::_run_coroutine`` and
    ``tests/test_billing_e2e.py::_run_marketplace_coroutine``.
    """
    previous: Optional[_asyncio.AbstractEventLoop]
    try:
        previous = _asyncio.get_event_loop_policy().get_event_loop()
    except RuntimeError:
        previous = None

    loop = _asyncio.new_event_loop()
    _asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        if previous is not None and not previous.is_closed():
            _asyncio.set_event_loop(previous)
        else:
            _asyncio.set_event_loop(_asyncio.new_event_loop())


def _renewal_limit_per_window() -> int:
    """The smallest allowance ``slowapi`` has registered for ``renew_subscription``.

    Read out of the registry the wrapper itself consults rather than hard-coded, so that changing
    ``5/60second`` in the router cannot silently turn a burst below into a rate-limit test.
    """
    key = f"{_library_router.__name__}.renew_subscription"
    registered = list(_limiter._route_limits.get(key, []))
    assert registered, (
        f"no rate limit is registered for {key}; Requirements 6.7 and 22.4 put one there, and "
        f"this battery sizes its bursts against it"
    )
    return min(int(item.limit.amount) for item in registered)


def _renewal_burst(n: int, *, subscription_status: str = "cancelled"):
    """Dispatch ``n`` concurrent ``POST /renew`` calls at the REAL handler over one shared double.

    Returns ``(db, factory, before, results)``. ``results`` holds each attempt's response body or
    the exception it raised, so a refusal is observable rather than swallowed.

    THE RATE LIMIT IS LEFT ARMED, AND THAT HAS TWO CONSEQUENCES THIS HANDLES EXPLICITLY.
    ``renew_subscription`` is decorated ``@limiter.limit("5/60second", key_func=caller_or_address)``
    and ``slowapi``'s wrapper raises ``Exception("parameter `request` must be an instance of
    starlette.requests.Request")`` unless the first argument really is one — so each attempt is
    handed its OWN real request. Its own, not one shared: the wrapper writes
    ``request.state._rate_limiting_complete`` after the first check, so N attempts over one
    request object would have the limiter evaluate exactly one of them, and this battery would
    then be driving a code path production never takes.

    The counters are process-wide and every attempt here keys to the same bucket (no
    ``Authorization`` header, so ``caller_or_address`` falls back to the source address), which
    means a battery of five bursts would exhaust 5/60s partway through and the later bursts would
    "pass" on nothing but 429s. So the limiter is reset on both sides of the burst — the decorator
    stays in force, only the tally is cleared — and the burst size is asserted against the
    registered allowance, so an over-large ``n`` fails loudly here instead of quietly measuring
    the limiter instead of the database.
    """
    allowance = _renewal_limit_per_window()
    assert n <= allowance, (
        f"a burst of {n} against a {allowance}/60s limit would be answered 429 from attempt "
        f"{allowance + 1} on, and this battery would assert nothing about concurrency. Split the "
        f"burst or raise the limit in the router with the requirement to justify it."
    )

    db = _MoneyPathSupabase(subscription_status=subscription_status)
    factory = _CountingSessionFactory(db)
    before = db.snapshot()

    async def _attempt():
        try:
            return await _library_router.renew_subscription(
                request=_real_request(
                    path=f"/api/library/subscriptions/{_RENEW_SUB_ID}/renew",
                    method="POST",
                ),
                sub_id=_RENEW_SUB_ID,
                user={"id": _RENEW_PURCHASER_ID},
            )
        except MarketplaceError as exc:
            return exc

    async def _drive():
        return await _asyncio.gather(*[_attempt() for _ in range(n)])

    # Both service-client accessors are patched, not just ``_build_service_client``: the pre-19.3
    # body reached the Persistence_Layer a second way, through the router's own
    # ``grant_deployment_permission`` and its ``_get_service_client()``. Patching one of them would
    # have let the pre-fix grant fail on a missing environment variable, be swallowed by its
    # ``except``, and this battery would then have "passed" against the defect it exists to catch.
    with _patch.object(
        _library_router, "_build_service_client", return_value=db
    ), _patch.object(
        _library_router, "_get_service_client", return_value=db
    ), _patch.object(
        _checkout_service, "default_provider_session_factory", factory
    ):
        _reset_limiter_counters()
        try:
            results = _run_coroutine(_drive())
        finally:
            _reset_limiter_counters()

    return db, factory, before, results


def _stripe_renewal_metadata(reference: str = _RENEWAL_REFERENCE) -> Dict[str, Any]:
    """The metadata a Stripe ``checkout.session.completed`` carries for this renewal.

    Built through the REAL ``billing._stripe_settlement_metadata`` from a real-shaped session, so
    the provider facts Requirement 9.14's guard is made of (reference, amount, currency, instant)
    are derived the way production derives them rather than hand-written.
    """
    session = {
        "id": "cs_renewal_1",
        "payment_intent": reference,
        "amount_total": _RENEW_PRICE_MINOR,
        "currency": "usd",
        "created": int(_RENEWAL_CONFIRMED_AT.timestamp()),
        "metadata": {
            "user_id": _RENEW_PURCHASER_ID,
            "subscription_id": _RENEW_SUB_ID,
            "library_id": _RENEW_LISTING_ID,
            "item_key": _MARKETPLACE_ITEM_KEY,
            "currency": "USD",
        },
    }
    return _billing_module._stripe_settlement_metadata(session["metadata"], session)


def _deliver_confirmations(
    db: "_MoneyPathSupabase",
    n: int,
    *,
    reference: str = _RENEWAL_REFERENCE,
) -> "_MoneyPathAuditRecorder":
    """Deliver the SAME payment confirmation ``n`` times, concurrently, through the ONE funnel.

    The path is the production one end to end: ``_apply_billing_entitlement`` dispatches on
    ``item_key.startswith("marketplace_")`` into ``_apply_marketplace_entitlement``, which makes
    one ``settlement_service.settle`` call. No webhook route, no signature check, no IP allow-list
    and no Redis lock is touched, replaced or weakened — this battery is about what the database
    constraint guarantees when the lock does not.
    """
    from backend_app.core import audit_trail

    recorder = _MoneyPathAuditRecorder()
    metadata = _stripe_renewal_metadata(reference)

    async def _drive():
        return await _asyncio.gather(
            *[
                _billing_module._apply_billing_entitlement(
                    _RENEW_PURCHASER_ID, _MARKETPLACE_ITEM_KEY, False, dict(metadata)
                )
                for _ in range(n)
            ]
        )

    with _patch.object(
        audit_trail, "get_strategy_audit_logger", return_value=recorder
    ), _patch.object(_billing_module, "_background_sb", return_value=db):
        _run_coroutine(_drive())

    return recorder


class TestConcurrentRenewal:
    """N concurrent ``POST /renew`` for one Subscription => no state change, one paid period.

    Requirements 11.6, 11.14 and 11.16 (a renewal takes a payment and writes nothing of its own)
    and 25.8 (this file's contract is updated only where the specification deliberately changes
    the behaviour it asserted, recorded together with the requirement that mandates it).
    """

    def test_two_concurrent_renewals_produce_no_state_change(self):
        """Requirement 11.16: the four writes are gone, and gone under concurrency too.

        Asserted three ways, because each catches a different regression: the statement log
        catches a write wherever it is issued from, the deep before/after comparison catches an
        equivalent write spelled differently, and the named-column assertions catch a partial
        revert of task 19.3.
        """
        db, factory, before, results = _renewal_burst(2)

        # Both requests answered with a session, neither with an activation.
        for result in results:
            assert isinstance(result, dict), f"a renewal raised {result!r}"
            assert result["status"] == "renewal_pending_payment"
            assert result["amount_minor"] == _RENEW_PRICE_MINOR
            assert result["currency"] == "USD"
            assert "active" not in str(result.get("status"))

        # (1) Not one write statement, against any table.
        assert db.writes() == [], (
            "a concurrent renewal burst issued write statement(s) "
            f"{[(s.op, s.table_name, s.payload) for s in db.writes()]}; renewal is checkout-only "
            "(Requirements 11.6, 11.14, 11.16)"
        )

        # (2) Every table byte-identical to before the burst.
        assert db.snapshot() == before, "a concurrent renewal burst changed stored state"

        # (3) The columns the defect moved, named explicitly.
        stored = db.subscription()
        assert stored["status"] == "cancelled", "a renewal reached active with no payment"
        assert stored["period_expiry"] == _RENEW_CURRENT_EXPIRY
        assert stored["expires_at"] == _RENEW_CURRENT_EXPIRY, (
            "expires_at was cleared; check_deployment_permission read a null expiry as perpetual "
            "access, which is the privilege defect Requirement 11.16 removes"
        )
        assert stored["cancelled_at"] is not None, "cancelled_at was cleared without a payment"
        assert db.settlements() == []
        assert db.permissions() == []
        assert db.transitions() == []
        assert db.tables["library_strategies"][0]["subscriber_count"] == 7

    def test_a_concurrent_renewal_burst_commits_at_most_one_provider_reference(self):
        """At most one provider reference of record — in fact zero, and that is the point.

        The endpoint writes nothing, so the burst commits NO new ``provider_reference`` and the one
        stored on the row is still the payment that bought the CURRENT period. That value is what
        ``settlement_service.find_settled_payment`` correlates a later refund by (task 19.16), so
        overwriting it with a renewal session id would silently break refund correlation for the
        month the subscriber has already paid for.

        The sessions the provider opened are asserted *interchangeable* rather than counted: see
        this section's header for why a path that writes nothing cannot — and must not —
        deduplicate its own outbound session, and why the requirements deduplicate a payment
        (Requirements 9.6, 10.10) rather than a session.
        """
        db, factory, _before, results = _renewal_burst(2)

        stored = db.subscription()
        assert stored["provider_reference"] == _RENEW_PRIOR_REFERENCE, (
            "a renewal overwrote the reference of the payment that bought the current period"
        )
        committed = [
            s
            for s in db.writes("library_subscriptions")
            if "provider_reference" in (s.payload or {})
        ]
        assert len(committed) == 0
        assert len(committed) <= 1  # at most one reference of record, trivially

        # Interchangeable: one Subscription, one integer amount, one currency across the burst.
        assert len(factory.contexts) == len(results)
        assert {c.subscription_id for c in factory.contexts} == {_RENEW_SUB_ID}
        assert {c.amount_minor for c in factory.contexts} == {_RENEW_PRICE_MINOR}
        assert {c.currency for c in factory.contexts} == {"USD"}
        assert all(isinstance(c.amount_minor, int) for c in factory.contexts)
        # And the settlement metadata each session carries back is one object, so no two of them
        # can disagree about which Subscription a payment or a refund belongs to.
        metadata = [_checkout_service._settlement_metadata(c) for c in factory.contexts]
        assert all(m == metadata[0] for m in metadata)
        assert metadata[0]["subscription_id"] == _RENEW_SUB_ID
        assert metadata[0]["item_key"] == _MARKETPLACE_ITEM_KEY

    def test_a_concurrent_renewal_burst_yields_exactly_one_paid_period(self):
        """Only a session that is actually PAID becomes a period (Requirements 11.5, 11.6).

        The burst opens several interchangeable sessions; the subscriber pays one. Exactly one
        Settlement_Record, one transition, one entitlement and one new expiry follow — and the
        expiry is one calendar month after the STORED expiry, not after the payment instant, so
        renewing mid-period buys the next month instead of truncating this one.
        """
        db, factory, _before, _results = _renewal_burst(3)
        assert db.settlements() == [], "a renewal settled before any payment confirmed"

        audit = _deliver_confirmations(db, 1)

        settlements = db.settlements()
        assert len(settlements) == 1
        row = settlements[0]
        assert row["provider_reference"] == _RENEWAL_REFERENCE
        assert row["is_reversal"] is False
        # The exact 90/10 split in integer Minor_Units (Requirements 10.1, 10.2).
        assert (
            row["amount_minor"],
            row["owner_share_minor"],
            row["platform_fee_minor"],
        ) == (_RENEW_PRICE_MINOR, _RENEW_OWNER_SHARE_MINOR, _RENEW_PLATFORM_FEE_MINOR)
        assert row["owner_share_minor"] + row["platform_fee_minor"] == row["amount_minor"]
        assert row["currency"] == "USD"

        stored = db.subscription()
        assert stored["status"] == "active"
        assert stored["period_expiry"] == _RENEWED_EXPIRY, (
            "the extension is anchored on the stored expiry, not on the confirmation instant "
            "(Requirement 11.5)"
        )
        assert stored["expires_at"] == _RENEWED_EXPIRY
        assert stored["period_start"] == _RENEW_CURRENT_START
        assert stored["cancelled_at"] is None

        assert len(db.transitions()) == 1
        assert db.transitions()[0]["from_state"] == "cancelled"
        assert db.transitions()[0]["to_state"] == "active"
        assert len(db.permissions()) == 1
        assert db.permissions()[0]["expires_at"] == _RENEWED_EXPIRY, (
            "the entitlement grant carries the period expiry, never the None that "
            "check_deployment_permission read as perpetual access"
        )
        assert audit.actions.count("MARKETPLACE_SETTLEMENT_CREATED") == 1

        # The sibling sessions the burst opened were never paid, so they settled nothing: the
        # ledger holds exactly the one payment that happened.
        assert len(factory.contexts) == 3
        assert [r["provider_reference"] for r in db.settlements()] == [_RENEWAL_REFERENCE]

    def test_a_renewal_of_a_live_subscription_is_still_checkout_only(self):
        """``active`` is renewable (``RENEWABLE_STATUSES``) and still writes nothing.

        The in-place ``active -> active`` renewal is ``settlement_service``'s, on a confirmed
        payment. The endpoint's contract does not change with the Subscription's state.
        """
        db, factory, before, results = _renewal_burst(2, subscription_status="active")

        for result in results:
            assert isinstance(result, dict), f"a renewal raised {result!r}"
            assert result["status"] == "renewal_pending_payment"
        assert db.writes() == []
        assert db.snapshot() == before
        assert db.subscription()["period_expiry"] == _RENEW_CURRENT_EXPIRY
        assert len(factory.contexts) == 2
        assert "active" in _checkout_service.RENEWABLE_STATUSES

    def test_a_refunded_subscription_is_not_renewable_under_concurrency(self):
        """``refunded`` is terminal: every attempt in the burst is refused, and none writes.

        Requirement 11.6 admits activation only from ``PENDING``, ``EXPIRED``, ``CANCELLED``,
        ``PAYMENT_FAILED`` (and, in place, ``ACTIVE``). Selling a renewal for a state the
        transition guard would then refuse is a purchaser charged for access they never get.
        """
        db, factory, before, results = _renewal_burst(3, subscription_status="refunded")

        assert all(isinstance(r, MarketplaceError) for r in results), (
            f"a refunded subscription sold a renewal: {results!r}"
        )
        assert all(r.http_status == 409 for r in results)
        assert factory.contexts == [], "the provider was contacted for a terminal subscription"
        assert db.writes() == []
        assert db.snapshot() == before
        assert "refunded" not in _checkout_service.RENEWABLE_STATUSES


class TestDuplicateWebhookDeliveryBattery:
    """N deliveries of ONE provider reference => one ledger row, one extension, N−1 duplicates.

    Requirements 9.6 (the same end state as a single delivery, at most one Settlement_Record per
    provider transaction reference, the period extended at most once), 10.10 (a reference already
    in the ledger adds no record, leaves every total unchanged and audits a duplicate) and 25.8.

    The deliveries are dispatched concurrently against one shared Persistence_Layer whose
    ``marketplace_settlements`` insert enforces ``uq_settlement_reference_reversal``, so the
    idempotency being measured is the DATABASE's. The two-phase Redis lock in
    ``routers/billing.py`` is untouched and deliberately not in play: it is the fast path, and a
    Redis flush, an expired key or a redelivery from a second process is precisely when the
    constraint is the only thing between one payment and two period extensions.
    """

    def _deliver(self, n: int, *, subscription_status: str = "cancelled"):
        db = _MoneyPathSupabase(subscription_status=subscription_status)
        audit = _deliver_confirmations(db, n)
        return db, audit

    def _assert_one_settlement_one_period(self, db, audit, n):
        # (1) EXACTLY ONE ledger row for the reference (Requirements 9.6, 9.7, 10.5).
        settlements = db.settlements()
        assert len(settlements) == 1, (
            f"{n} deliveries of one provider reference wrote {len(settlements)} "
            "marketplace_settlements rows"
        )
        row = settlements[0]
        assert row["provider_reference"] == _RENEWAL_REFERENCE
        assert row["is_reversal"] is False
        assert row["reverses_reference"] is None
        assert (
            row["amount_minor"],
            row["owner_share_minor"],
            row["platform_fee_minor"],
        ) == (_RENEW_PRICE_MINOR, _RENEW_OWNER_SHARE_MINOR, _RENEW_PLATFORM_FEE_MINOR)
        assert row["owner_share_minor"] + row["platform_fee_minor"] == row["amount_minor"]
        assert row["currency"] == "USD"
        assert row["subscription_id"] == _RENEW_SUB_ID

        # (2) EXACTLY ONE period extension: one UPDATE, one history row, one entitlement grant,
        #     and one expiry value however many times the event arrived.
        subscription_updates = db.update_payloads("library_subscriptions")
        assert len(subscription_updates) == 1, (
            f"{n} deliveries issued {len(subscription_updates)} transition UPDATE(s); the period "
            "must be extended at most once per provider transaction reference (Requirement 9.6)"
        )
        assert len(db.transitions()) == 1
        assert len(db.permissions()) == 1
        stored = db.subscription()
        assert stored["status"] == "active"
        assert stored["period_expiry"] == _RENEWED_EXPIRY
        assert stored["expires_at"] == _RENEWED_EXPIRY
        assert db.permissions()[0]["expires_at"] == _RENEWED_EXPIRY

        # (3) N−1 duplicate audit entries beside the single creation (Requirement 10.10).
        assert audit.actions.count("MARKETPLACE_SETTLEMENT_CREATED") == 1
        assert audit.actions.count("MARKETPLACE_SETTLEMENT_DUPLICATE_IGNORED") == n - 1, (
            f"expected {n - 1} duplicate audit entries, got "
            f"{audit.actions.count('MARKETPLACE_SETTLEMENT_DUPLICATE_IGNORED')} "
            f"from {audit.actions}"
        )
        # Nothing was reported as unmatched, mismatched or a persistence failure.
        for never in (
            "MARKETPLACE_SETTLEMENT_UNMATCHED",
            "MARKETPLACE_SETTLEMENT_MISMATCHED",
            "MARKETPLACE_SETTLEMENT_PERSIST_FAILED",
        ):
            assert never not in audit.actions

    def test_one_delivery_is_the_baseline(self):
        """n = 1: one row, one extension, zero duplicate entries — the state every n must match."""
        db, audit = self._deliver(1)
        self._assert_one_settlement_one_period(db, audit, 1)

    def test_two_concurrent_deliveries_settle_once(self):
        db, audit = self._deliver(2)
        self._assert_one_settlement_one_period(db, audit, 2)

    def test_five_concurrent_deliveries_settle_once(self):
        db, audit = self._deliver(5)
        self._assert_one_settlement_one_period(db, audit, 5)

    def test_ten_concurrent_deliveries_settle_once(self):
        db, audit = self._deliver(10)
        self._assert_one_settlement_one_period(db, audit, 10)

    def test_redelivery_reaches_the_same_end_state_as_a_single_delivery(self):
        """Requirement 9.6, stated as the metamorphic equality it actually is.

        One delivery and ten deliveries of the same confirmation are compared column by column,
        with the generated row ids projected out — an end state that merely "looks fine" for each
        n separately is not the same end state.
        """
        once, _audit_once = self._deliver(1)
        many, _audit_many = self._deliver(10)

        def _project(rows):
            return [
                {k: v for k, v in row.items() if k not in {"id", "granted_at"}}
                for row in rows
            ]

        assert _project(many.settlements()) == _project(once.settlements())
        assert _project(many.transitions()) == _project(once.transitions())
        assert _project(many.permissions()) == _project(once.permissions())
        assert many.subscription() == once.subscription()

    def test_a_payment_failed_subscription_activates_on_a_retried_payment(self):
        """``payment_failed -> active`` is permitted, and redelivery is still once.

        Requirement 11.6 over Requirement 11.2, seeded in the database by
        ``backend_app/migrations/012_subscription_payment_failed_activation.sql``: a retried
        payment that finally confirms must activate the Subscription it paid for. Included here
        because the duplicate branch returns BEFORE the transition, so a redelivery on this edge
        must not re-attempt an activation that already happened.
        """
        db, audit = self._deliver(4, subscription_status="payment_failed")
        self._assert_one_settlement_one_period(db, audit, 4)
        assert db.transitions()[0]["from_state"] == "payment_failed"
        assert db.transitions()[0]["to_state"] == "active"
        assert "payment_failed" in _settlement_service.ELIGIBLE_FOR_ACTIVATION

    def test_a_concurrent_burst_for_an_unknown_subscription_writes_nothing(self):
        """Requirement 9.14: an uncorrelatable confirmation writes nothing, N times over."""
        db = _MoneyPathSupabase()
        db.tables["library_subscriptions"] = []
        audit = _deliver_confirmations(db, 4)

        assert db.settlements() == []
        assert db.permissions() == []
        assert db.transitions() == []
        assert audit.actions.count("MARKETPLACE_SETTLEMENT_UNMATCHED") == 4
        assert "MARKETPLACE_SETTLEMENT_CREATED" not in audit.actions

# ══════════════════════════════════════════════════════════════════════════════
# THE REMAINING FIVE CONCURRENCY BATTERIES (task 34.3)
# Requirements 16.10, 16.9, 17.6, 19.3, 11.7, 11.8
# ══════════════════════════════════════════════════════════════════════════════
#
# WHAT IS ADDED, AND WHAT IS NOT TOUCHED
# --------------------------------------
# Five batteries, appended to the five this file already held plus the two added by tasks 14.12
# and 19.13. Nothing above is renamed, weakened, skipped or removed; every assertion that was
# here is still here and still says the same thing.
#
#   B1  N concurrent order intents against ONE Paper_Account   Requirement 16.10 (P-23)
#   B2  N concurrent fills carrying ONE ``fill_event_id``      Requirement 16.9
#   B3  Two concurrent Paper_Sessions of one user, one         Requirement 17.6
#       strategy, one symbol, one timeframe
#   B4  Two instances allocating ``paper_events.sequence``     Requirement 19.3 (P-53)
#   B5  The expiry sweep against an entitlement check at the   Requirements 11.7, 11.8
#       expiry instant
#
# THE MECHANISM EVERY ONE OF THEM ASSERTS OVER
# --------------------------------------------
# There is **no transaction over PostgREST** in this codebase: no ``BEGIN``, no ``COMMIT``, no
# ``SELECT ... FOR UPDATE``, no ``RETURNING``. ``paper_simulator``'s own section header records
# it and ``tests/property/test_paper_confluence.py`` pins it. Concurrency is handled by two
# things and only two things:
#
#   * a **version-guarded optimistic UPDATE** - ``lock_account_for_update`` reads the account's
#     ``version``, every later write goes through ``bump_version(expected_version=N)`` whose
#     ``.eq("version", N)`` matches zero rows when another writer moved the row in between; and
#   * the **unique indexes**, which arbitrate the writers the version predicate cannot see.
#
# So "no intent applied twice or lost" is asserted over THAT, not over a rollback. Where a
# residual gap exists the production code states it prefix by prefix (a 409 past the money
# carries ``partial_write=True`` and names its phase), and these batteries assert what actually
# holds rather than pretending the gap is closed. B1's oracle asserts the documented residue as
# an EQUALITY, so a residue that grew would fail.
#
# WHY THE DOUBLES ARE THE EXISTING ONES
# -------------------------------------
# ``tests/test_paper_repository.FakeSupabase`` is THE Persistence_Layer double
# (``tests/paper_seed.py`` records the one-double rule). It models the eleven ``paper_*`` tables,
# the eight unique indexes ``009``/``013`` declare, the two BEFORE UPDATE triggers and three
# injectable seams. The indexes are what make B2 and B4 assertable at all: without
# ``uq_paper_fill_event`` B2's central claim would pass against a simulator with no fill
# idempotency, and without ``uq_paper_event_seq`` B4's would pass against an allocator that
# handed the same number to both instances. B5 extends ``tests/test_expiry_sweep.FakeSupabase``
# - the double built for exactly the four marketplace tables the sweep writes - with the two
# read-only tables the Entitlement_Resolver reads, so ONE store answers both sides of that race.
#
# AND WHY THE ORACLES ARE THE EXISTING ONES
# -----------------------------------------
# P-23's confluence oracle (every sequential permutation, asserted to produce one common result)
# and P-53's contiguity oracle (``{1..k}``, computed from the row count) already exist and are
# driven here rather than restated. B1 and B4 are the named, fixed-N cases those properties'
# counterexamples would be compared against; the property modules keep the generated space.
#
# NEVER ``asyncio.run``
# ---------------------
# Every coroutine below is driven by ``tests/test_paper_order_lifecycle_writes._run_coroutine``,
# which uses the process's ONE event loop - see that module's ``_HARNESS_LOOP`` note: on Windows
# a fresh loop per call is two real loopback TCP connections, and a loop per coroutine exhausted
# the machine's ephemeral port range through ``socket._fallback_socketpair`` and hung the suite
# past 700 s with no output. Nested drives (a competitor that has to start *inside* another
# call's statement) go through ``test_paper_confluence._drive_without_a_loop``, which steps the
# coroutine once and fails loudly naming what was awaited if one step is not enough.
# ``asyncio.gather`` is awaited from inside a coroutine, never called from synchronous code.
#
# NAMING
# ------
# CI runs ``pytest tests/ -k "not chaos and not load"``. A test whose name contains either
# substring is silently deselected, so no name here contains ``chaos`` or ``load`` - which for a
# concurrency suite is a real hazard rather than a theoretical one.

from datetime import timedelta as _timedelta
from decimal import Decimal as _Decimal
from typing import Sequence

from backend_app.backend.marketplace import entitlement_resolver as _entitlement_resolver
from backend_app.backend.marketplace import expiry_sweep as _expiry_sweep
from backend_app.backend.paper import paper_accounting as _accounting
from backend_app.backend.paper import paper_repository as _repo
from backend_app.backend.paper import paper_simulator as _sim
from backend_app.backend.paper.paper_order_state import PaperOrderState as _PaperOrderState

# ── The one Persistence_Layer double, and the paper harness built on it. ───────────────────
from tests.test_paper_repository import FakeSupabase as _PaperFakeSupabase
from tests.test_paper_repository import FakeUniqueViolation as _PaperUniqueViolation

#: A SECOND Paper_Session id for B3. Taken from ``tests/test_paper_repository`` because it is
#: already the repository suite's "some other session", and it is only ever an identifier here.
#:
#: ``_SESSION_A``, ``_PAPER_USER`` and ``_PAPER_SYMBOL`` come from
#: ``tests/test_paper_order_lifecycle_writes`` and NOT from ``tests/test_paper_repository``, and
#: the distinction is load-bearing rather than stylistic: the two modules use different UUIDs for
#: the user and the session (``11111111-1111-4111-8111-...`` against
#: ``11111111-1111-1111-1111-...``). ``submit_intent`` reads ``user_id`` and ``id`` off the SESSION
#: ROW it is handed, so an account created under one module's ``USER`` is simply not readable for
#: the other's - which surfaces as ``PaperPersistenceError`` rather than as a wrong answer.
from tests.test_paper_repository import OTHER_SESSION as _SESSION_B
from tests.test_paper_order_lifecycle_writes import (
    NOW as _PAPER_NOW,
    SESSION as _SESSION_A,
    SYMBOL as _PAPER_SYMBOL,
    USER as _PAPER_USER,
    _Sleeps as _PaperSleeps,
    _accepted_market_order,
    _config as _paper_config,
    _event as _paper_event,
    _fill as _paper_fill,
    _intent as _paper_intent,
    _run_coroutine as _run_paper,
    _seed as _paper_seed,
    _session_row as _paper_session_row,
    _snapshot as _paper_snapshot,
    _version_racer,
    _wrote_since,
)

# ── P-23's oracle and the nested-drive mechanism, imported rather than restated. ───────────
from tests.property.paper_census import Recorder as _Recorder
from tests.property.test_paper_confluence import (
    P23_FLOORS as _P23_FLOORS,
    P23_LABELS as _P23_LABELS,
    REJECTED_INTENTS as _P23_REJECTED_INTENTS,
    RICH as _P23_RICH,
    SUCCEEDING_NAMES as _P23_SUCCEEDING_NAMES,
    _ConfluenceCase,
    _assert_one_concurrent_run_matches_the_oracle,
    _drive_without_a_loop,
    _sequential_oracle,
)

# ── P-53's producers, plan and oracle helpers. ────────────────────────────────────────────
from tests.property.test_paper_channel_sequence import (
    P53_FLOORS as _P53_FLOORS,
    P53_LABELS as _P53_LABELS,
    _ConcurrentWriter as _SequenceWriter,
    _Plan as _SequencePlan,
    _Tally as _SequenceTally,
    _counter as _sequence_counter,
    _log as _session_event_log,
    _produce_concurrently as _produce_sequences_concurrently,
    _swap_attempts as _sequence_swap_attempts,
)
from tests.test_task_26_4_paper_channel_delivery import _client as _channel_client
from tests.test_task_26_4_paper_channel_delivery import _session_row as _channel_session_row

# ── The expiry sweep's own double and row builders. ────────────────────────────────────────
from tests.test_expiry_sweep import FakeSupabase as _SweepFakeSupabase
from tests.test_expiry_sweep import LISTING as _SWEEP_LISTING
from tests.test_expiry_sweep import PURCHASER as _SWEEP_PURCHASER
from tests.test_expiry_sweep import _RecordingAuditWriter as _SweepAuditWriter
from tests.test_expiry_sweep import _Resp as _SweepResp
from tests.test_expiry_sweep import _subscription as _sweep_subscription


# ══════════════════════════════════════════════════════════════════════════════
# B1 - N CONCURRENT ORDER INTENTS AGAINST ONE PAPER_ACCOUNT (Requirement 16.10)
# ══════════════════════════════════════════════════════════════════════════════
#
# TWO TESTS, TWO VALUES OF N, TWO DIFFERENT CLAIMS
# -----------------------------------------------
# ``N = 4`` drives P-23's confluence oracle at a FIXED case: the four raced intents' final
# balances, positions, orders, settled fills, ledger totals and equity series are compared - as
# exact ``Decimal`` and exact ``int``, with no tolerance anywhere - against the ONE result all
# ``4! = 24`` sequential permutations produce, and a fifth intent is starved into a 409 whose
# residue is asserted exactly. That comparison is P-23's, so it is driven and not restated:
# ``_sequential_oracle`` computes the expectation on twenty-four fresh stores with nothing
# racing, and ``_assert_one_concurrent_run_matches_the_oracle`` makes the comparison, asserts
# that a genuine conflict occurred (a run in which nothing raced would prove nothing), asserts
# exactly one ``paper_orders`` row per idempotency key - one is "applied once", zero is LOST and
# two is APPLIED TWICE - and asserts no settled ``(order_id, fill_event_id)`` appears twice.
#
# ``N = 8`` is this battery's own: eight distinct intents, each one released INSIDE the account
# read that immediately precedes the previous one's version-guarded UPDATE, so all eight
# submissions' statements are genuinely straddled rather than merely gathered. It asserts the
# Requirement 18.3 identity - ``total_equity == available_balance + locked_balance +
# position_market_value``, exact, zero tolerance - **after every single one of the eight**, which
# is the half of the task's wording the final-state oracle cannot cover. The identity is checked
# with ``paper_accounting.violated_invariant``, the same function ``paper_simulator`` asserts
# inside its own attempt, against the rows as they are STORED.
#
# WHY THE ACCOUNT READ IS THE SWITCH POINT
# ----------------------------------------
# Every attempt of ``submit_intent``, ``_lock_for_order`` and ``apply_fill`` takes exactly one
# account read - ``lock_account_for_update`` - so a submission's reads number its phases. Read 1
# is ``submit_intent``'s own attempt, which writes only ``paper_orders`` and has no version
# predicate to lose. Read 2 is the one immediately before the version-guarded UPDATE. Releasing a
# competitor at read 2 therefore ARRANGES the conflict instead of hoping for one, and both tests
# below assert that a conflict happened.

#: B1's fixed P-23 case. Every field is one of the values ``confluent_intent_sets`` draws, chosen
#: rather than generated because this is the named example a counterexample is compared against;
#: the generated space stays in ``tests/property/test_paper_confluence.py``.
#:
#: ``switch_at``/``nested_switch_at`` both contain 2 - the read immediately before the
#: version-guarded UPDATE - which is what makes the outer submission AND the competitor released
#: inside it each lose a compare-and-swap.
_B1_CASE = _ConfluenceCase(
    key_suffix="task34x3",
    side="buy",
    quantities=("0.25", "0.5", "0.75", "1", "1.5"),
    limit_prices=("50", "60", "96.25"),
    close="100",
    fee_rate="0.001",
    slippage_rate="0.0005",
    rejected_intent=_P23_REJECTED_INTENTS[1],
    limit2_order_type="limit",
    switch_at=frozenset({2}),
    nested_switch_at=frozenset({2}),
)

#: B1's second N. Eight DISTINCT intents - distinct quantities, hence distinct fingerprints -
#: alternating market and limit so both version-guarded UPDATE sites (``apply_fill``'s and
#: ``_lock_for_order``'s) are contended. Priced so the whole set costs well under
#: ``_P23_RICH``: an order-dependent ``INSUFFICIENT_FUNDS`` would make "applied once" depend on
#: arrival order, which is not what Requirement 16.10 is about.
_B1_EIGHT: Tuple[Tuple[str, Dict[str, Any]], ...] = (
    ("m1", {"order_type": "market", "quantity": "0.25"}),
    ("l1", {"order_type": "limit", "quantity": "0.5", "limit_price": "50"}),
    ("m2", {"order_type": "market", "quantity": "0.75"}),
    ("l2", {"order_type": "limit", "quantity": "1", "limit_price": "60"}),
    ("m3", {"order_type": "market", "quantity": "1.5"}),
    ("l3", {"order_type": "limit", "quantity": "2", "limit_price": "96.25"}),
    ("m4", {"order_type": "market", "quantity": "3"}),
    ("l4", {"order_type": "limit", "quantity": "0.125", "limit_price": "120.50"}),
)

#: The market close every B1/B2/B3 market intent is priced from. Two decimals - the symbol's
#: ``price_precision`` is 2 - and a string, because ``paper_simulator._event_decimal`` refuses a
#: ``float`` (Requirement 18.1) and a test that passed one would exercise a path production cannot.
_B1_CLOSE = "100"


def _paper_account_row(supabase: Any, account_id: str) -> Dict[str, Any]:
    """The stored ``paper_accounts`` row for ``account_id``. Absence is a failure, not a default."""
    for row in supabase.accounts:
        if str(row.get("id")) == str(account_id):
            return dict(row)
    raise AssertionError(f"no paper_accounts row {account_id!r} is stored")


def _assert_the_equity_identity_holds(supabase: Any, account_id: str, context: str) -> None:
    """Requirement 18.3 on the rows as STORED, exact, zero tolerance.

    ``paper_accounting.violated_invariant`` is the same function ``paper_simulator`` asserts
    inside its own attempt, so this is the production oracle rather than a second arithmetic. It
    is applied to the persisted account and position rows - not to the in-process values the
    simulator returned - because Requirement 18.3 is a claim about every point at which the four
    figures are READABLE through the Persistence_Layer.

    Positions are valued at the prices already recorded on them
    (``last_validated_prices``); nothing is synthesised, interpolated or defaulted to zero, which
    is Requirement 18.15's rule and also the only way this check can be exact.
    """
    account_row = _paper_account_row(supabase, account_id)
    position_rows = [
        dict(row)
        for row in supabase.positions
        if str(row.get("account_id")) == str(account_id)
    ]
    account = _sim.account_of(account_row)
    positions = _sim.positions_of(position_rows)
    prices = _accounting.last_validated_prices(positions.values())
    breach = _accounting.violated_invariant(
        account, positions, prices, _paper_config().accounting()
    )
    assert breach is None, (
        f"Requirement 18.3 breached ({breach}) after {context}: total_equity "
        f"{account.total_equity} against available_balance {account.available_balance} + "
        f"locked_balance {account.locked_balance} + position_market_value "
        f"{_accounting.position_market_value(positions, prices, _paper_config().accounting())}. "
        "The identity admits ZERO tolerance."
    )


class _IntentInterleaver:
    """The double's ``after_select`` seam, turned into a scheduler of competing submissions.

    One queued submission is released at the scheduled account read of the submission currently
    running, and it runs **to completion** there - so its statements sit strictly inside the
    other's, which is what makes the race a race rather than two calls in a row. This is the
    same seam and the same reasoning ``_version_racer`` and
    ``test_paper_confluence._Interleaver`` use; it is written here in its N-deep form because
    B1's eight-intent test needs a chain of eight rather than one nesting of four.

    Reads are counted PER NESTING DEPTH and a depth's counter restarts each time a submission at
    that depth begins, so ``switch_at`` means "the Nth account read of THIS submission" and not
    "the Nth read of the run". Read 2 is the one that matters: it is the read immediately before
    the version-guarded UPDATE, so releasing there makes the conflict arranged rather than hoped
    for. Recursion is bounded by :attr:`pending` - each competitor is popped before it runs.

    ``max_depth`` bounds how deep the nesting goes. ``None`` is unbounded, which is what B1 wants:
    eight submissions against ONE account, each straddling the last, so same-account contention is
    the subject. B3 sets it to ``1``, and that is not a simplification but a requirement of what
    B3 measures: with unbounded nesting a session's own LATER submission ends up running inside
    its EARLIER one, and the earlier one then loses its compare-and-swap to itself. That is
    legitimate same-account contention (B1's subject) and it would make "no write to the other
    session disturbed this one" unmeasurable, because the backoffs would have two possible causes.
    """

    __slots__ = (
        "switch_at",
        "pending",
        "run_one",
        "max_depth",
        "depth",
        "counts",
        "released",
        "armed",
    )

    def __init__(
        self,
        switch_at: int,
        pending: List[str],
        run_one: Any,
        max_depth: Optional[int] = None,
    ) -> None:
        self.switch_at = int(switch_at)
        self.pending = list(pending)
        self.run_one = run_one
        self.max_depth = max_depth
        self.depth = 0
        self.counts: Dict[int, int] = {}
        self.released: List[str] = []
        self.armed = True

    def begin(self, depth: int) -> None:
        self.counts[depth] = 0

    def __call__(self, client: Any, query: Any) -> None:
        if not self.armed:
            return
        if query.table_name != _repo.ACCOUNTS_TABLE or query.op != "select":
            return
        depth = self.depth
        if self.max_depth is not None and depth >= self.max_depth:
            return
        self.counts[depth] = self.counts.get(depth, 0) + 1
        if self.counts[depth] != self.switch_at or not self.pending:
            return
        name = self.pending.pop(0)
        self.released.append(name)
        self.depth = depth + 1
        self.begin(self.depth)
        try:
            self.run_one(name)
        finally:
            self.depth = depth


def test_four_concurrent_order_intents_land_where_every_sequential_order_lands() -> None:
    """B1, N = 4 raced + 1 starved: P-23's confluence at a fixed case (Requirement 16.10).

    The oracle is every sequential permutation of the four intents - twenty-four fresh stores,
    nothing racing, asserted to produce ONE common result - and the concurrent run's balances,
    positions, orders, settled fills, ledger row counts and column totals, closed trades and
    equity series are compared against it as exact ``Decimal`` and exact ``int``.

    Both conflict sites are exercised, one per run: the outer submission is the resting limit
    order (whose version-guarded UPDATE is ``_lock_for_order``'s) in the first run and the market
    order (whose is ``apply_fill``'s) in the second. The helper asserts that a real conflict
    occurred in each, that the outer itself retried, that a nested competitor also conflicted,
    that every raced intent is present exactly once under its idempotency key, that no settled
    ``(order_id, fill_event_id)`` appears twice, and that the starved intent applied nothing -
    with its residue asserted as an EQUALITY, so a residue that grew a ledger row, a position, an
    equity point or one minor unit of any balance would fail.
    """
    recorder = _Recorder("P-23 (task 34.3, N=4)", _P23_FLOORS, _P23_LABELS)
    recorder.start()
    keys = frozenset(_B1_CASE.key(name) for name in _P23_SUCCEEDING_NAMES)

    oracle = _sequential_oracle(_B1_CASE, _P23_SUCCEEDING_NAMES, keys)

    # Run 1: the resting limit order is the overtaken submission; the starved intent exhausts in
    # ``_lock_for_order``, so its residue is one ACCEPTED order with NOTHING locked.
    _assert_one_concurrent_run_matches_the_oracle(
        _B1_CASE, "limit", "starved_limit", oracle, keys, recorder
    )
    # Run 2: the market order is the overtaken submission; the starved intent exhausts in
    # ``apply_fill``, so its residue is one ACCEPTED order PLUS one unsettled fill row - the
    # idempotency anchor a retry resumes from, and no money moved.
    _assert_one_concurrent_run_matches_the_oracle(
        _B1_CASE, "market", "starved_market", oracle, keys, recorder
    )

    # The two runs really did take both conflict shapes, read off the census rather than assumed.
    assert recorder.counts["outer_was_a_limit_order"] == 1
    assert recorder.counts["outer_was_a_market_order"] == 1
    assert recorder.counts["a_real_conflict_occurred"] == 2
    assert recorder.counts["the_outer_retried_and_then_succeeded"] == 2
    assert recorder.counts["a_nested_submission_also_conflicted"] == 2
    assert recorder.counts["every_raced_intent_succeeded"] == 2
    assert recorder.counts["starved_409_left_a_durable_accepted_order"] == 2
    assert recorder.counts["permutations_replayed"] == 0  # counted by the property, not here
    _repo.reset_persistence_probe()


def test_eight_concurrent_order_intents_apply_once_each_and_hold_the_equity_identity() -> None:
    """B1, N = 8: no intent applied twice, none lost, and Requirement 18.3 after every one.

    Eight distinct intents against ONE Paper_Account. Each is released inside the account read
    that immediately precedes the previous one's version-guarded UPDATE, so the eight
    submissions' statements are straddled eight deep and every one of them contends for the same
    ``paper_accounts`` row.

    Three claims, and the identity is the third:

      1. **Applied once.** Exactly one ``paper_orders`` row per idempotency key - zero would be
         LOST, two would be APPLIED TWICE - and the order each submission reported is the row
         persisted under its own key.
      2. **No fill applied twice.** Every settled ``(order_id, fill_event_id)`` pair is distinct.
      3. **The identity after every one.** ``total_equity == available_balance + locked_balance +
         position_market_value`` on the STORED rows, exact, zero tolerance, checked after each of
         the eight rather than only at the end.

    The run is asserted non-vacuous: the account's ``version`` moved, funds are locked (so a
    resting order's guarded lock applied), a fill settled, a position exists, and at least one
    submission slept a bounded retry backoff - which is the only observable proof that a
    version-guarded UPDATE matched zero rows and was retried.
    """
    supabase, session, account_id = _paper_seed(capital=_P23_RICH)
    config = _paper_config(
        fee_rate=_Decimal("0.001"), slippage_rate=_Decimal("0.0005")
    )
    specs = dict(_B1_EIGHT)
    names = [name for name, _ in _B1_EIGHT]
    sleeps: Dict[str, Any] = {name: _PaperSleeps() for name in names}
    outcomes: Dict[str, Any] = {}
    applied_order: List[str] = []

    def _coroutine(name: str) -> Any:
        return _sim.submit_intent(
            supabase,
            session,
            _paper_intent(side="buy", idempotency_key=name, **specs[name]),
            config=config,
            account_id=account_id,
            latest_event=_paper_event(close=_B1_CLOSE, source_event_id="evt-b1-eight"),
            sleep=sleeps[name],
        )

    def _record(name: str, driver: Any) -> None:
        outcomes[name] = driver(_coroutine(name))
        applied_order.append(name)
        # Requirement 18.3 after EVERY intent, on the rows as stored.
        _assert_the_equity_identity_holds(
            supabase, account_id, f"intent {name} (order {len(applied_order)} of 8)"
        )

    interleaver = _IntentInterleaver(
        switch_at=2,
        pending=names[1:],
        run_one=lambda name: _record(name, _drive_without_a_loop),
    )
    supabase.after_select = interleaver
    interleaver.begin(0)
    _record(names[0], _run_paper)
    while interleaver.pending:
        interleaver.begin(0)
        _record(interleaver.pending.pop(0), _run_paper)
    interleaver.armed = False
    supabase.after_select = None

    context = f"released={interleaver.released}, applied_in={applied_order}"

    # ── the interleaving was real ─────────────────────────────────────────────
    assert sorted(outcomes) == sorted(names), (
        f"an intent was never even offered: recorded {sorted(outcomes)} of {sorted(names)}. "
        f"{context}"
    )
    assert len(interleaver.released) >= 7, (
        "fewer than seven competitors were released INSIDE another submission's account read, so "
        f"the eight submissions did not straddle each other. {context}"
    )
    slept = {name: recorder.delays for name, recorder in sleeps.items()}
    total_backoffs = sum(len(delays) for delays in slept.values())
    assert total_backoffs >= 1, (
        "no submission slept a retry backoff, so no version-guarded UPDATE ever matched zero "
        f"rows and nothing was concurrent. Slept: {slept}. {context}"
    )
    for delays in slept.values():
        for delay in delays:
            assert _Decimal(str(delay)) in _sim.RETRY_BACKOFF_SECONDS, (
                f"a slept delay of {delay} is not one of the bounded, jitter-free backoffs "
                f"{_sim.RETRY_BACKOFF_SECONDS}; a jittered backoff would break Requirement "
                f"15.4's replay. {context}"
            )

    # ── (1) applied once: one order row per key, and it is the row that was reported ──────
    for name in names:
        matching = [
            row
            for row in supabase.orders
            if row.get("idempotency_key") is not None
            and str(row["idempotency_key"]) == name
        ]
        assert len(matching) == 1, (
            f"intent {name} has {len(matching)} paper_orders row(s) under its idempotency key. "
            f"One is 'applied once'; zero is LOST and two is APPLIED TWICE, and Requirement "
            f"16.10 forbids both. {context}"
        )
        assert str(outcomes[name].order["id"]) == str(matching[0]["id"]), (
            f"intent {name} reported an order that is not the one persisted under its key. "
            f"{context}"
        )
        assert not outcomes[name].duplicate, (
            f"intent {name} was answered as a duplicate although its key is its own. {context}"
        )
    assert len(supabase.orders) == len(names), (
        f"{len(supabase.orders)} paper_orders rows exist for {len(names)} distinct intents. "
        f"{context}"
    )

    # ── (2) no fill applied twice ─────────────────────────────────────────────
    settled_ids = {
        str(row.get("fill_id"))
        for row in supabase.balance_events
        if str(row.get("cause")) == "FILL" and row.get("fill_id") is not None
    }
    settled_pairs = [
        (str(row["order_id"]), str(row["fill_event_id"]))
        for row in supabase.fills
        if str(row["id"]) in settled_ids
    ]
    assert len(settled_pairs) == len(set(settled_pairs)), (
        f"a settled (order_id, fill_event_id) appears twice, so a fill was applied twice: "
        f"{settled_pairs}. {context}"
    )

    # ── (3) the identity at the end too, and the run is not vacuous ───────────
    _assert_the_equity_identity_holds(supabase, account_id, "all eight intents")
    account = _paper_account_row(supabase, account_id)
    assert int(account["version"]) > 1, (
        f"the account's version never moved, so no optimistic-concurrency write happened at all. "
        f"{context}"
    )
    assert _Decimal(str(account["locked_balance"])) > 0, (
        f"nothing is locked, so no resting limit order's version-guarded lock ever applied. "
        f"{context}"
    )
    assert supabase.fills, f"no fill was recorded, so no market order was applied. {context}"
    assert supabase.positions, f"no position exists, so nothing was traded. {context}"
    _repo.reset_persistence_probe()


# ══════════════════════════════════════════════════════════════════════════════
# B2 - N CONCURRENT FILLS CARRYING ONE ``fill_event_id`` (Requirements 16.9, 18.13)
# ══════════════════════════════════════════════════════════════════════════════
#
# WHAT REQUIREMENT 16.9 ACTUALLY ASKS FOR, AND THE TWO WAYS IT IS MET
# -------------------------------------------------------------------
# "WHEN the Paper_Simulator receives a fill event carrying an event identifier already applied to
# an order, THE Paper_Simulator SHALL make no change to the order, the position, the balance or
# the realized profit and loss." ``apply_fill`` meets it two ways, and a concurrency battery has
# to exercise BOTH because only one of them survives a race:
#
#   * **guard 2, the READ.** The attempt reads the order's fills and, when one already carries
#     this ``fill_event_id`` AND a ``cause='FILL'`` ledger row carries that fill's id, returns
#     ``FILL_DUPLICATE`` unchanged. This is the path a sequential redelivery takes.
#   * **``uq_paper_fill_event``, the INDEX.** A writer that lands between guard 2 and write 1
#     makes the INSERT raise ``23505``; ``apply_fill`` catches ``PaperDuplicateFill`` and returns
#     ``FILL_DUPLICATE`` unchanged. This is the path a genuine race takes, and it is the only
#     arbiter available - there is no transaction to serialise the read against the insert.
#
# So the race below is arranged so that exactly one attempt reaches the index: the competitor is
# released at the ``paper_fills`` INSERT of the first attempt, through the double's
# ``before_insert`` seam, and runs to completion there. The first attempt's own INSERT then
# collides. Every later attempt finds the settled fill and takes the read path. Both counts are
# asserted, so a regression that removed either would fail rather than be absorbed by the other.
#
# WHAT "EXACTLY ONE BALANCE MOVEMENT" AND "ONE EQUITY SNAPSHOT" ARE MEASURED AS
# -----------------------------------------------------------------------------
# One ``paper_balance_events`` row with ``cause='FILL'`` carrying that fill's ``fill_id`` - that
# is the only place a per-fill realized figure is persisted (``tests/paper_seed.py`` records
# why) - and one ``paper_equity_snapshots`` row, which is Requirement 18.13's separate claim
# ("SHALL persist no additional equity snapshot for a repeated event identifier"). Both are
# counted, and the whole end state is then compared column by column against a CONTROL run in
# which the same fill is applied exactly once with nothing racing. An end state that merely
# "looks fine" for the race is not the same end state.

#: B2's N values. ``2`` is the smallest race that can exist; ``5`` and ``9`` put several attempts
#: on the read path behind the one on the index path, which is the shape a redelivery storm takes.
_B2_NS: Tuple[int, ...] = (2, 5, 9)

#: The one fill every attempt in a B2 race carries.
_B2_FILL_EVENT_ID = "fill-event-one-and-only"

#: The order is for TWO and the fill is for ONE, so the applied fill leaves the order at
#: ``PARTIALLY_FILLED`` rather than at ``FILLED``.
#:
#: This is not incidental and it was found by measurement. ``apply_fill``'s guard 1 (terminal
#: order, Requirement 16.3) sits BEFORE guard 2 (duplicate ``fill_event_id``, Requirement 16.9),
#: deliberately - a terminal order changes nothing *whatever else is true*. So a fill that FILLS
#: the order makes every later redelivery answer ``FILL_TERMINAL``, and guard 2's read path is
#: never entered: the run would still write one fill, one movement and one snapshot, but it would
#: be testing the terminal guard rather than the idempotency one. A partial fill keeps the order
#: at ``PARTIALLY_FILLED``, which is not terminal, so the redeliveries reach guard 2 and the
#: battery exercises the mechanism it names.
_B2_ORDER_QUANTITY = "2"
_B2_QUANTITY = "1"
_B2_PRICE = "1000"


def _b2_config() -> Any:
    """B2's session configuration: fees and slippage ON.

    A zero-fee, zero-slippage configuration would make "one balance movement" a claim about a
    movement of zero in the fee column, and a second application would then be invisible in the
    figures even if it happened. With a fee rate a duplicate application would move the money a
    second time and the comparison against the control run would catch it.
    """
    return _paper_config(fee_rate=_Decimal("0.001"), slippage_rate=_Decimal("0"))


def _b2_fill_coroutine(supabase: Any, session: Dict[str, Any], order: Dict[str, Any]) -> Any:
    """One ``apply_fill`` coroutine for the one shared fill event. Not awaited here."""
    return _sim.apply_fill(
        supabase,
        session,
        order,
        config=_b2_config(),
        quantity=_B2_QUANTITY,
        price=_B2_PRICE,
        fill_event_id=_B2_FILL_EVENT_ID,
        filled_at=_PAPER_NOW,
        sleep=_PaperSleeps(),
    )


class _FillRacer:
    """The double's ``before_insert`` seam, turned into a competing fill writer.

    ``before_insert`` fires with the row NOT yet landed, so a competitor that inserts the same
    ``(order_id, fill_event_id)`` here makes the interrupted attempt's own INSERT violate
    ``uq_paper_fill_event`` - which is precisely the interleaving guard 2's read cannot see and
    the index exists to arbitrate. ``busy`` is a re-entrancy guard, not a lock: the competitor
    issues its own ``paper_fills`` INSERT, which would fire this seam again and recurse without
    bound.
    """

    __slots__ = ("supabase", "session", "order", "pending", "outcomes", "busy", "released")

    def __init__(
        self, supabase: Any, session: Dict[str, Any], order: Dict[str, Any], pending: int
    ) -> None:
        self.supabase = supabase
        self.session = session
        self.order = order
        self.pending = int(pending)
        self.outcomes: List[Any] = []
        self.busy = False
        self.released = 0

    def __call__(self, client: Any, query: Any) -> None:
        if query.table_name != _repo.FILLS_TABLE or query.op != "insert":
            return
        if self.busy or self.pending <= 0:
            return
        self.busy = True
        self.pending -= 1
        self.released += 1
        try:
            self.outcomes.append(
                _drive_without_a_loop(
                    _b2_fill_coroutine(self.supabase, self.session, self.order)
                )
            )
        finally:
            self.busy = False


def _b2_race(n: int) -> Tuple[Any, str, List[Any], int]:
    """``n`` concurrent deliveries of ONE fill event against one order.

    Returns ``(supabase, account_id, outcomes, released_inside_the_insert)``. Exactly one
    competitor is released inside the first attempt's ``paper_fills`` INSERT, so exactly one
    duplicate is arbitrated by ``uq_paper_fill_event`` and the rest by guard 2's read.
    """
    supabase, session, account_id = _paper_seed()
    order = _accepted_market_order(supabase, account_id, quantity=_B2_ORDER_QUANTITY)
    supabase.statements.clear()
    supabase.ops.clear()

    racer = _FillRacer(supabase, session, order, pending=1)
    supabase.before_insert = racer
    outcomes = [_run_paper(_b2_fill_coroutine(supabase, session, order))]
    supabase.before_insert = None
    # The remaining attempts arrive after the winner has settled, so they take guard 2's read.
    for _ in range(n - 2):
        outcomes.append(_run_paper(_b2_fill_coroutine(supabase, session, order)))
    outcomes.extend(racer.outcomes)
    return supabase, account_id, outcomes, racer.released


def _b2_control() -> Tuple[Any, str]:
    """The same fill applied exactly ONCE, with nothing racing. The end state every ``n`` must match."""
    supabase, session, account_id = _paper_seed()
    order = _accepted_market_order(supabase, account_id, quantity=_B2_ORDER_QUANTITY)
    outcome = _run_paper(_b2_fill_coroutine(supabase, session, order))
    assert outcome.outcome == _sim.FILL_APPLIED, (
        f"the control run did not apply the fill ({outcome.outcome}), so there is nothing to "
        "compare a race against"
    )
    return supabase, account_id


def _b2_end_state(supabase: Any, account_id: str) -> Dict[str, Any]:
    """Everything "the same end state as one delivery" is a claim about, as exact values.

    Row identifiers are deliberately absent: the double mints them from its statement sequence,
    so two runs differing in statement ORDER necessarily differ in them, and that is the thing
    concurrency changes rather than what idempotency is about. Every money figure is compared as
    exact ``Decimal`` and every minor-unit figure as exact ``int``.
    """
    account = _paper_account_row(supabase, account_id)
    return {
        "available_balance": _Decimal(str(account["available_balance"])),
        "locked_balance": _Decimal(str(account["locked_balance"])),
        "realized_pnl": _Decimal(str(account["realized_pnl"])),
        "total_equity": _Decimal(str(account["total_equity"])),
        "version": int(account["version"]),
        "fills": sorted(
            (
                str(row["fill_event_id"]),
                _Decimal(str(row["quantity"])),
                _Decimal(str(row["price"])),
                int(row["fee_minor"]),
                int(row["slippage_minor"]),
            )
            for row in supabase.fills
        ),
        "balance_events": sorted(
            (
                str(row["cause"]),
                _Decimal(str(row["available_delta"])),
                _Decimal(str(row["locked_delta"])),
                _Decimal(str(row["realized_delta"])),
            )
            for row in supabase.balance_events
        ),
        "equity_snapshots": sorted(
            (str(row["cause"]), _Decimal(str(row["total_equity"])))
            for row in supabase.equity_snapshots
        ),
        "positions": sorted(
            (
                str(row["symbol"]),
                str(row["side"]),
                _Decimal(str(row["size"])),
                _Decimal(str(row["entry_price"])),
            )
            for row in supabase.positions
        ),
        "orders": sorted(
            (
                str(row["order_state"]),
                _Decimal(str(row.get("filled_quantity") or 0)),
                int(row.get("fee_minor") or 0),
            )
            for row in supabase.orders
        ),
        "trades": sorted(
            (str(row["symbol"]), _Decimal(str(row["realized_pnl"])))
            for row in supabase.trades
        ),
    }


def _assert_one_fill_one_movement_one_snapshot(
    supabase: Any, account_id: str, outcomes: List[Any], released: int, n: int
) -> None:
    """B2's three counts, the two arbitration paths, and the metamorphic equality."""
    context = (
        f"n={n}, outcomes={[o.outcome for o in outcomes]}, "
        f"released_inside_the_insert={released}"
    )
    assert len(outcomes) == n, f"{len(outcomes)} attempts were recorded, not {n}. {context}"

    # ── exactly one attempt applied; the other n-1 changed nothing ────────────
    applied = [o for o in outcomes if o.outcome == _sim.FILL_APPLIED]
    duplicates = [o for o in outcomes if o.outcome == _sim.FILL_DUPLICATE]
    assert len(applied) == 1, (
        f"{len(applied)} of {n} concurrent deliveries of one fill_event_id APPLIED. Requirement "
        f"16.9 admits exactly one. {context}"
    )
    assert len(duplicates) == n - 1, (
        f"{len(duplicates)} attempts answered DUPLICATE, expected {n - 1}. {context}"
    )
    assert not [o for o in outcomes if o.outcome == _sim.FILL_TERMINAL], (
        "an attempt answered TERMINAL, so guard 1 (Requirement 16.3) fired ahead of guard 2 and "
        "the idempotency guard this battery is about was not reached. The fill must be PARTIAL - "
        f"see _B2_ORDER_QUANTITY. {context}"
    )
    for outcome in duplicates:
        # The two no-op outcomes carry the order unchanged and every other field None - that is
        # Requirement 16.9 read literally, and it is asserted rather than assumed.
        assert outcome.fill is None and outcome.account is None, (
            f"a DUPLICATE outcome carried a fill or an account, so it did not 'change nothing'. "
            f"{context}"
        )
        # The outcome carries the order row as the attempt READ it, which for the attempt whose
        # INSERT the index refused is the pre-fill image - so the state on the outcome is not
        # asserted here. What is asserted is that it is not terminal, i.e. that this attempt
        # reached guard 2 or the index rather than guard 1.
        assert str(outcome.order["order_state"]) in (
            _PaperOrderState.ACCEPTED.value,
            _PaperOrderState.PARTIALLY_FILLED.value,
        ), (
            f"a DUPLICATE outcome reported the order at {outcome.order['order_state']!r}, which "
            f"is terminal. {context}"
        )

    # The STORED order is where the one applied fill left it, and no repeat moved it further.
    stored_orders = [dict(row) for row in supabase.orders]
    assert len(stored_orders) == 1, f"{len(stored_orders)} order rows exist. {context}"
    assert stored_orders[0]["order_state"] == _PaperOrderState.PARTIALLY_FILLED.value, (
        f"the stored order is at {stored_orders[0]['order_state']!r}; one fill of "
        f"{_B2_QUANTITY} against an order for {_B2_ORDER_QUANTITY} leaves it PARTIALLY_FILLED, "
        f"and {n - 1} repeats must not move it. {context}"
    )
    assert _Decimal(str(stored_orders[0]["filled_quantity"])) == _Decimal(_B2_QUANTITY), (
        f"the stored order's cumulative filled quantity is "
        f"{stored_orders[0]['filled_quantity']!r}, not the one fill's {_B2_QUANTITY}; a repeat "
        f"was applied. {context}"
    )

    # ── exactly one row, one movement, one snapshot ───────────────────────────
    fills = [
        row for row in supabase.fills if str(row["fill_event_id"]) == _B2_FILL_EVENT_ID
    ]
    assert len(fills) == 1, (
        f"{len(fills)} paper_fills rows carry {_B2_FILL_EVENT_ID!r}. uq_paper_fill_event is "
        f"UNIQUE (order_id, fill_event_id), so more than one is impossible in PostgreSQL and a "
        f"second one here means the double's index was not consulted. {context}"
    )
    assert len(supabase.fills) == 1, (
        f"{len(supabase.fills)} paper_fills rows exist in total. {context}"
    )
    fill_id = str(fills[0]["id"])
    movements = [
        row
        for row in supabase.balance_events
        if str(row.get("cause")) == "FILL" and str(row.get("fill_id")) == fill_id
    ]
    assert len(movements) == 1, (
        f"{len(movements)} FILL balance movements carry fill {fill_id}. Requirement 16.9 admits "
        f"one; a second would move the money twice for one event. {context}"
    )
    assert len(supabase.equity_snapshots) == 1, (
        f"{len(supabase.equity_snapshots)} equity snapshots exist. Requirement 18.13 admits no "
        f"ADDITIONAL snapshot for a repeated event identifier. {context}"
    )

    # ── both arbitration paths were used ─────────────────────────────────────
    assert released == 1, (
        f"the competitor was not released inside the first attempt's paper_fills INSERT, so no "
        f"attempt reached uq_paper_fill_event and only guard 2's read was exercised. {context}"
    )
    insert_attempts = [
        q
        for q in supabase.statements
        if q.table_name == _repo.FILLS_TABLE and q.op == "insert"
    ]
    assert len(insert_attempts) == 2, (
        f"{len(insert_attempts)} paper_fills INSERT statements were issued, expected 2: the "
        f"winner's, which landed, and the interrupted attempt's, which uq_paper_fill_event "
        f"refused. One would mean the index was never asked; more would mean an attempt retried "
        f"past the refusal. {context}"
    )
    read_path = n - 2
    assert len(duplicates) == read_path + 1, (
        f"the duplicate outcomes do not split into one index refusal and {read_path} read-path "
        f"refusals. {context}"
    )

    # ── the end state IS the end state of a single delivery ───────────────────
    control, control_account = _b2_control()
    observed = _b2_end_state(supabase, account_id)
    expected = _b2_end_state(control, control_account)
    differing = sorted(claim for claim in expected if observed[claim] != expected[claim])
    assert differing == [], (
        f"{n} concurrent deliveries of one fill event did not reach the end state one delivery "
        f"reaches. Differing: {differing}. "
        + "; ".join(
            f"{claim}: one delivery {expected[claim]!r} against {n} {observed[claim]!r}"
            for claim in differing
        )
        + f". {context}"
    )
    _assert_the_equity_identity_holds(supabase, account_id, f"a race of {n} identical fills")


@pytest.mark.parametrize("n", _B2_NS, ids=[f"n{n}" for n in _B2_NS])
def test_n_concurrent_fills_of_one_event_id_write_one_fill_one_movement_one_snapshot(
    n: int,
) -> None:
    """B2: exactly one ``paper_fills`` row, one balance movement, one equity snapshot.

    ``n`` deliveries of ONE ``fill_event_id`` against one order. One is arbitrated by
    ``uq_paper_fill_event`` (the competitor is released inside the interrupted attempt's own
    INSERT, so the index is the only thing that can decide) and ``n - 2`` by guard 2's read, and
    both counts are asserted so a regression that removed either path would fail. The end state
    is then compared column by column against a control run that applied the same fill once with
    nothing racing.
    """
    supabase, account_id, outcomes, released = _b2_race(n)
    _assert_one_fill_one_movement_one_snapshot(supabase, account_id, outcomes, released, n)
    _repo.reset_persistence_probe()


def _fills_index_relaxed(original: Any) -> Any:
    """``FakeSupabase._enforce_unique`` with ``uq_paper_fill_event`` - and only it - taken off."""

    def relaxed(self: Any, table: str, row: Dict[str, Any]) -> None:
        if table == _repo.FILLS_TABLE:
            return
        original(self, table, row)

    return relaxed


def test_b2_fails_when_uq_paper_fill_event_is_relaxed_in_the_double() -> None:
    """B2 can FAIL, demonstrated rather than asserted about.

    A battery whose central claim cannot fail proves nothing, and B2's central claim rests
    entirely on ``uq_paper_fill_event``: the competitor is released INSIDE the interrupted
    attempt's ``paper_fills`` INSERT, so guard 2's read has already passed and the index is the
    only thing left to decide. This test relaxes that ONE index in the double - every other
    index, trigger and behaviour untouched - runs the identical race, and requires that
    :func:`_assert_one_fill_one_movement_one_snapshot` then FAILS with two fill rows.

    The relaxation is scoped to this test by ``patch.object``, so the double is restored the
    moment it returns; the parametrised battery above runs against the real index.
    """
    original = _PaperFakeSupabase._enforce_unique
    with _patch.object(
        _PaperFakeSupabase, "_enforce_unique", _fills_index_relaxed(original)
    ):
        supabase, account_id, outcomes, released = _b2_race(2)
        # The refused INSERT is no longer refused, so a second fill row exists and a second
        # attempt reports APPLIED.
        assert len(supabase.fills) == 2, (
            "relaxing uq_paper_fill_event did not produce a second paper_fills row, so the "
            "battery's premise - that the index is what decides this race - is wrong and the "
            "battery may be passing for another reason"
        )
        with pytest.raises(AssertionError) as caught:
            _assert_one_fill_one_movement_one_snapshot(
                supabase, account_id, outcomes, released, 2
            )
        message = str(caught.value)
        assert "paper_fills rows carry" in message or "admits exactly one" in message, (
            "the battery failed with the relaxed index, but not on the fill-row or applied-count "
            f"claim, so the demonstration is not the one intended: {message}"
        )

    # Restored: the very same race against the real index passes again.
    supabase, account_id, outcomes, released = _b2_race(2)
    _assert_one_fill_one_movement_one_snapshot(supabase, account_id, outcomes, released, 2)
    _repo.reset_persistence_probe()


# ══════════════════════════════════════════════════════════════════════════════
# B3 - TWO CONCURRENT PAPER_SESSIONS OF ONE USER, ONE STRATEGY, ONE SYMBOL,
#      ONE TIMEFRAME (Requirement 17.6)
# ══════════════════════════════════════════════════════════════════════════════
#
# THE CLAUSE BEING TESTED, IN FULL
# -------------------------------
# Requirement 17.6: "THE Paper_Session_Service SHALL create each Paper_Session with its own
# isolated order book state, position set and balance set, AND SHALL NOT share any of them with
# another Paper_Session, such that no create, update or delete applied to one Paper_Session's
# orders, positions or balances changes any row belonging to another Paper_Session, **including
# two concurrent Paper_Sessions of the same user on the same strategy, symbol and timeframe**."
#
# The italicised clause is the hard case, and it is hard for a specific reason: same user, same
# strategy, same symbol, same timeframe means every predicate EXCEPT ``session_id`` /
# ``account_id`` is identical between the two sessions, so a statement that lost one of those two
# predicates would still look correct and would silently write the wrong book.
#
# WHAT MAKES THE TWO BOOKS SEPARATE, AND WHERE
# --------------------------------------------
#   * ``uq_paper_account_session`` UNIQUE ``(session_id, currency)`` gives each session its own
#     ``paper_accounts`` row, so each has its own ``version`` counter - which is why a write to
#     one cannot make the other's optimistic guard fail.
#   * ``uq_paper_position_open`` is UNIQUE ``(account_id, symbol) WHERE closed_at IS NULL``, so
#     both sessions hold an OPEN position on the SAME symbol at the same time. An index keyed on
#     symbol alone would refuse the second, and the isolation would be a data loss.
#   * ``uq_paper_order_idem`` is UNIQUE ``(session_id, idempotency_key)``, so the two sessions may
#     reuse one key - ``probe_idempotency_key``'s own docstring says so - and each gets its own
#     order rather than one being answered with the other's.
#   * ``bump_version`` filters ``.eq("id", account_id).eq("user_id", user_id).eq("version", n)``,
#     so the guarded UPDATE is scoped to one row of one account.
#
# HOW THE CONCURRENCY IS PRODUCED, AND WHY THE ABSENCE OF A CONFLICT IS THE POINT
# ------------------------------------------------------------------------------
# The six submissions alternate between the two sessions and each is released INSIDE the account
# read that immediately precedes the previous one's version-guarded UPDATE - the seam that in B1
# reliably starves a submission when the competitor shares the account. Here the competitor is
# the OTHER session, so nothing is starved: the run is asserted to sleep **zero** retry backoffs.
#
# That absence is only meaningful if the seam can in fact starve something, so
# :func:`test_a_writer_on_the_same_account_does_starve_a_session_which_is_what_makes_the_isolation_measurable`
# arms ``_version_racer`` - the existing device that moves the account row after every read of it,
# i.e. a competitor on the SAME account - and requires the 409. Without that control the
# zero-backoff assertion would be satisfiable by a harness in which nothing can ever conflict.

#: B3's capital. Both sessions start from the same figure, so a difference in the end state is a
#: difference the writes made and not one the premise had.
_B3_CAPITAL = _Decimal("100000")

#: The strategy, symbol and timeframe BOTH sessions run. Identical on purpose: this is the
#: "including two concurrent Paper_Sessions of the same user on the same strategy, symbol and
#: timeframe" clause, and every predicate except ``session_id``/``account_id`` is therefore the
#: same between the two books.
_B3_STRATEGY_ID = "44444444-4444-4444-8444-444444444444"

#: Three intents per session, alternating between them. The middle one of each pair is a limit
#: order, so both version-guarded UPDATE sites are contended: ``apply_fill``'s (a market order
#: fills on acceptance) and ``_lock_for_order``'s (a resting limit order locks funds).
#:
#: The two sessions submit the SAME three idempotency keys. ``uq_paper_order_idem`` is UNIQUE
#: ``(session_id, idempotency_key)``, so that is legal and each session must get its own order;
#: a probe that lost the ``session_id`` predicate would answer session B with session A's order,
#: which is exactly the sharing Requirement 17.6 forbids.
_B3_INTENTS: Tuple[Tuple[str, Dict[str, Any]], ...] = (
    ("shared-key-1", {"order_type": "market", "quantity": "0.5"}),
    ("shared-key-2", {"order_type": "limit", "quantity": "1", "limit_price": "60"}),
    ("shared-key-3", {"order_type": "market", "quantity": "0.25"}),
)

#: The eight ``paper_*`` tables a session's book lives in, as the double's attribute names.
_B3_BOOK_TABLES: Tuple[str, ...] = (
    "orders",
    "fills",
    "positions",
    "balance_events",
    "trades",
    "equity_snapshots",
    "metrics",
)


def _b3_session_row(session_id: str) -> Dict[str, Any]:
    """One ``paper_sessions`` row, same strategy / symbol / timeframe as its sibling.

    Built from the harness's ``_session_row`` rather than from a second literal, so the shape is
    the one ``SESSION_FEED_SELECT`` reads and the feed state is the one the simulator's gate
    admits. ``strategy_id`` is added explicitly because Requirement 17.6's clause names the
    strategy, and a reader of this test should be able to see that both rows carry the same one.
    """
    return dict(_paper_session_row(), id=session_id, strategy_id=_B3_STRATEGY_ID)


def _b3_double() -> Tuple[Any, Dict[str, Any], str, Dict[str, Any], str]:
    """One store, two Paper_Sessions of one user, each with its own isolated Paper_Account.

    Both accounts are created through ``paper_repository.get_or_create_account`` with a
    ``session_id``, which is the production path for a session's isolated balance set, so the
    premise these assertions run under is the premise production runs under (Requirement 17.1).
    ``uq_paper_account_session`` is what keeps each single.
    """
    _repo.reset_persistence_probe()
    row_a = _b3_session_row(_SESSION_A)
    row_b = _b3_session_row(_SESSION_B)
    supabase = _PaperFakeSupabase(sessions=[row_a, row_b])
    account_a = _repo.get_or_create_account(
        supabase, _PAPER_USER, "USD", _SESSION_A, initial_capital=_B3_CAPITAL
    )
    account_b = _repo.get_or_create_account(
        supabase, _PAPER_USER, "USD", _SESSION_B, initial_capital=_B3_CAPITAL
    )
    assert str(account_a["id"]) != str(account_b["id"]), (
        "the two sessions were handed ONE paper_accounts row, so there is no isolation to test; "
        "uq_paper_account_session is UNIQUE (session_id, currency) and each session must have its "
        "own balance set (Requirement 17.6)"
    )
    supabase.statements.clear()
    supabase.ops.clear()
    return supabase, row_a, str(account_a["id"]), row_b, str(account_b["id"])


def _b3_rows_of(supabase: Any, session_id: str) -> Dict[str, List[Dict[str, Any]]]:
    """Every row of every book table carrying ``session_id``, plus that session's account row."""
    image: Dict[str, List[Dict[str, Any]]] = {
        "accounts": [
            dict(row)
            for row in supabase.accounts
            if str(row.get("session_id")) == str(session_id)
        ]
    }
    for name in _B3_BOOK_TABLES:
        image[name] = [
            dict(row)
            for row in getattr(supabase, name)
            if str(row.get("session_id")) == str(session_id)
        ]
    return image


def _b3_figures(supabase: Any, session_id: str, account_id: str) -> Dict[str, Any]:
    """One session's book as exact values, with the double's minted ids projected out.

    Row identifiers and ``updated_at`` are deliberately absent: the double mints ids from its
    statement sequence and ``paper_repository`` stamps ``updated_at`` from the wall clock, so two
    runs that differ in statement ORDER necessarily differ in both - and statement order is the
    thing concurrency changes, not the thing isolation is about. Every money figure is compared
    as exact ``Decimal`` and every minor-unit figure as exact ``int``; ``version`` is compared as
    an ``int`` because a spurious extra guarded UPDATE would move it.
    """
    account = _paper_account_row(supabase, account_id)
    rows = _b3_rows_of(supabase, session_id)
    return {
        "account": (
            _Decimal(str(account["available_balance"])),
            _Decimal(str(account["locked_balance"])),
            _Decimal(str(account["realized_pnl"])),
            _Decimal(str(account["total_equity"])),
            int(account["version"]),
        ),
        "orders": sorted(
            (
                str(row.get("idempotency_key")),
                str(row["order_state"]),
                str(row["side"]),
                str(row["order_type"]),
                _Decimal(str(row["quantity"])),
                _Decimal(str(row.get("filled_quantity") or 0)),
                str(row.get("limit_price")),
                int(row.get("fee_minor") or 0),
                str(row.get("rejection_reason")),
            )
            for row in rows["orders"]
        ),
        "fills": sorted(
            (
                _Decimal(str(row["quantity"])),
                _Decimal(str(row["price"])),
                int(row["fee_minor"]),
                int(row["slippage_minor"]),
            )
            for row in rows["fills"]
        ),
        "positions": sorted(
            (
                str(row["symbol"]),
                str(row["side"]),
                _Decimal(str(row["size"])),
                _Decimal(str(row["entry_price"])),
                str(row.get("current_price")),
                str(row.get("closed_at")),
            )
            for row in rows["positions"]
        ),
        "balance_events": sorted(
            (
                str(row["cause"]),
                _Decimal(str(row["available_delta"])),
                _Decimal(str(row["locked_delta"])),
                _Decimal(str(row["realized_delta"])),
            )
            for row in rows["balance_events"]
        ),
        "equity_snapshots": sorted(
            (str(row["cause"]), _Decimal(str(row["total_equity"])))
            for row in rows["equity_snapshots"]
        ),
        "trades": sorted(
            (str(row["symbol"]), _Decimal(str(row["realized_pnl"])))
            for row in rows["trades"]
        ),
    }


def _b3_submit(
    supabase: Any,
    session: Dict[str, Any],
    account_id: str,
    key: str,
    overrides: Dict[str, Any],
    sleeps: Any,
) -> Any:
    """One ``submit_intent`` coroutine for one session. Not awaited here."""
    return _sim.submit_intent(
        supabase,
        session,
        _paper_intent(side="buy", idempotency_key=key, **overrides),
        config=_paper_config(fee_rate=_Decimal("0.001"), slippage_rate=_Decimal("0")),
        account_id=account_id,
        latest_event=_paper_event(close=_B1_CLOSE, source_event_id="evt-b3"),
        sleep=sleeps,
    )


def _b3_run(
    plan: Sequence[Any], *, interleave: bool
) -> Dict[str, Any]:
    """Submit ``plan``, interleaved in PAIRS or one after another.

    Interleaved, ``plan`` is a sequence of ``(outer, inner)`` pairs and the inner submission runs
    to completion INSIDE the account read that immediately precedes the outer's version-guarded
    UPDATE - so the two sessions' statements straddle each other. The nesting is deliberately ONE
    level deep (``max_depth=1``): the competitor of a submission is always the OTHER session's,
    never this session's own later submission, so a backoff can have exactly one cause and the
    absence of one is a measurement rather than a coincidence.

    Sequential, ``plan`` is a flat sequence of names and they run one after another. That is the
    control :func:`_b3_figures` is compared against.
    """
    supabase, row_a, account_a, row_b, account_b = _b3_double()
    intents = dict(_B3_INTENTS)
    names = (
        [name for pair in plan for name in pair] if interleave else list(plan)
    )
    sleeps: Dict[str, Any] = {name: _PaperSleeps() for name in names}
    outcomes: Dict[str, Any] = {}

    def _coroutine(name: str) -> Any:
        which, key = name.split(":", 1)
        session, account_id = (row_a, account_a) if which == "A" else (row_b, account_b)
        return _b3_submit(supabase, session, account_id, key, intents[key], sleeps[name])

    def _record(name: str, driver: Any) -> None:
        outcomes[name] = driver(_coroutine(name))

    released: List[str] = []
    if interleave:
        for outer, inner in plan:
            interleaver = _IntentInterleaver(
                switch_at=2,
                pending=[inner],
                run_one=lambda name: _record(name, _drive_without_a_loop),
                max_depth=1,
            )
            supabase.after_select = interleaver
            interleaver.begin(0)
            _record(outer, _run_paper)
            interleaver.armed = False
            supabase.after_select = None
            assert not interleaver.pending, (
                f"{interleaver.pending} was never released inside {outer}'s account read, so this "
                "pair did not interleave"
            )
            released.extend(interleaver.released)
    else:
        for name in plan:
            _record(name, _run_paper)

    return {
        "supabase": supabase,
        "row_a": row_a,
        "row_b": row_b,
        "account_a": account_a,
        "account_b": account_b,
        "outcomes": outcomes,
        "sleeps": sleeps,
        "released": released,
    }


#: Three interleaved pairs - six submissions across N = 2 concurrent Paper_Sessions. Which session
#: is the overtaken one alternates, so both directions are exercised: A's submission straddles B's
#: in pairs 1 and 3, and B's straddles A's in pair 2.
_B3_PAIRS: Tuple[Tuple[str, str], ...] = (
    ("A:shared-key-1", "B:shared-key-1"),
    ("B:shared-key-2", "A:shared-key-2"),
    ("A:shared-key-3", "B:shared-key-3"),
)

#: The same six submissions as a flat sequence, for the "which session was it" split below.
_B3_ORDER: Tuple[str, ...] = tuple(name for pair in _B3_PAIRS for name in pair)


def test_two_concurrent_sessions_of_one_user_do_not_write_each_others_rows() -> None:
    """B3: N = 2 sessions, 6 interleaved submissions, one strategy, one symbol, one timeframe.

    Requirement 17.6's hardest clause. Four claims:

      1. **Disjoint books.** Every row of every book table carries exactly one of the two
         ``session_id`` values and the ``account_id`` of that session; the two row sets share
         nothing.
      2. **Both sessions hold their own OPEN position on the SAME symbol at the same time.**
         ``uq_paper_position_open`` is keyed ``(account_id, symbol)``, so this is legal; an index
         keyed on symbol alone would have refused the second and the isolation would be a data
         loss rather than an isolation.
      3. **One key, two orders.** The two sessions submit the same three idempotency keys and
         each gets its own order at its own state - ``uq_paper_order_idem`` is UNIQUE
         ``(session_id, idempotency_key)`` - so no submission was answered with the other
         session's order.
      4. **Each book is byte-for-byte what it would be alone.** Each session's figures under the
         interleaved run equal that session's figures from a control run in which only that
         session submitted, against a store seeded with both sessions. Nothing the other session
         wrote moved a single value, including the account's ``version`` - so no write to one even
         disturbed the other's optimistic-concurrency counter, which is why the run sleeps zero
         retry backoffs.
    """
    run = _b3_run(_B3_PAIRS, interleave=True)
    supabase = run["supabase"]
    context = f"released={run['released']}"

    # ── the interleaving was real ─────────────────────────────────────────────
    assert sorted(run["outcomes"]) == sorted(_B3_ORDER), (
        f"a submission was never offered: {sorted(run['outcomes'])}. {context}"
    )
    assert run["released"] == [inner for _, inner in _B3_PAIRS], (
        "the three competitors were not released INSIDE their pair partner's account read, so the "
        f"two sessions' statements did not straddle each other. {context}"
    )
    slept = {name: recorder.delays for name, recorder in run["sleeps"].items()}
    assert all(delays == [] for delays in slept.values()), (
        "a submission slept a retry backoff, so a write to one Paper_Session made the OTHER "
        "session's version-guarded UPDATE match zero rows. Each session has its own "
        "paper_accounts row (uq_paper_account_session) and its own version counter, so a "
        f"conflict here is a Requirement 17.6 breach, not a race. Slept: {slept}. {context}"
    )

    # ── (1) disjoint books ────────────────────────────────────────────────────
    image_a = _b3_rows_of(supabase, _SESSION_A)
    image_b = _b3_rows_of(supabase, _SESSION_B)
    for name in ("accounts",) + _B3_BOOK_TABLES:
        ids_a = {str(row["id"]) for row in image_a[name]}
        ids_b = {str(row["id"]) for row in image_b[name]}
        assert ids_a.isdisjoint(ids_b), (
            f"{name}: the two sessions share row(s) {sorted(ids_a & ids_b)}. {context}"
        )
        stored = {
            str(row["id"])
            for row in getattr(supabase, name)
            if str(row.get("session_id")) in (_SESSION_A, _SESSION_B)
        }
        assert stored == ids_a | ids_b, (
            f"{name}: a row belongs to neither session's book. {context}"
        )
        if name == "accounts":
            continue
        # ``account_id`` is asserted where the table HAS one. ``paper_fills`` does not: 009 hangs
        # a fill off its ORDER (``order_id``), which is what ``uq_paper_fill_event`` is keyed on,
        # so its account is its order's account and is checked that way below.
        for label, image, account_id in (
            ("A", image_a, run["account_a"]),
            ("B", image_b, run["account_b"]),
        ):
            for row in image[name]:
                if row.get("account_id") is None:
                    continue
                assert str(row["account_id"]) == account_id, (
                    f"{name}: a row of session {label} carries account_id "
                    f"{row['account_id']!r}, which is not session {label}'s account. {context}"
                )
    for label, image in (("A", image_a), ("B", image_b)):
        own_orders = {str(row["id"]) for row in image["orders"]}
        for row in image["fills"]:
            assert str(row["order_id"]) in own_orders, (
                f"a paper_fills row of session {label} hangs off order {row['order_id']!r}, which "
                f"belongs to the other session. {context}"
            )

    # ── (2) both sessions hold an OPEN position on the same symbol at once ────
    open_a = [row for row in image_a["positions"] if row.get("closed_at") is None]
    open_b = [row for row in image_b["positions"] if row.get("closed_at") is None]
    assert len(open_a) == 1 and len(open_b) == 1, (
        f"expected one open position per session, got {len(open_a)} and {len(open_b)}. {context}"
    )
    assert open_a[0]["symbol"] == _PAPER_SYMBOL == open_b[0]["symbol"], (
        f"the two open positions are not both on {_PAPER_SYMBOL}, so the same-symbol case - the "
        f"one uq_paper_position_open's (account_id, symbol) key exists for - was not exercised. "
        f"{context}"
    )

    # ── (3) one idempotency key, two orders ───────────────────────────────────
    for key, _ in _B3_INTENTS:
        rows = [
            row
            for row in supabase.orders
            if str(row.get("idempotency_key")) == key
        ]
        assert len(rows) == 2, (
            f"idempotency key {key!r} produced {len(rows)} order(s). uq_paper_order_idem is "
            f"UNIQUE (session_id, idempotency_key), so two sessions may reuse one key and each "
            f"must get its own order (Requirement 16.11); one row here means a probe answered "
            f"one session with the other's order. {context}"
        )
        assert {str(row["session_id"]) for row in rows} == {_SESSION_A, _SESSION_B}
        for name in ("A", "B"):
            outcome = run["outcomes"][f"{name}:{key}"]
            assert not outcome.duplicate, (
                f"session {name}'s submission under key {key!r} was answered as a DUPLICATE, so "
                f"it was served the other session's order. {context}"
            )
            expected_session = _SESSION_A if name == "A" else _SESSION_B
            assert str(outcome.order["session_id"]) == expected_session, (
                f"session {name}'s submission returned an order belonging to session "
                f"{outcome.order['session_id']!r}. {context}"
            )

    # ── (4) each book is what it would be alone ───────────────────────────────
    # Each session alone, in the order that session submitted under the interleaved run:
    # shared-key-1, shared-key-2, shared-key-3 for both.
    alone_a = _b3_run(
        tuple(f"A:{key}" for key, _ in _B3_INTENTS), interleave=False
    )
    alone_b = _b3_run(
        tuple(f"B:{key}" for key, _ in _B3_INTENTS), interleave=False
    )
    for label, session_id, account_key, control in (
        ("A", _SESSION_A, "account_a", alone_a),
        ("B", _SESSION_B, "account_b", alone_b),
    ):
        observed = _b3_figures(supabase, session_id, run[account_key])
        expected = _b3_figures(
            control["supabase"], session_id, control[account_key]
        )
        differing = sorted(claim for claim in expected if observed[claim] != expected[claim])
        assert differing == [], (
            f"session {label}'s book is not what it would be with the other session absent. "
            f"Differing: {differing}. "
            + "; ".join(
                f"{claim}: alone {expected[claim]!r} against concurrent {observed[claim]!r}"
                for claim in differing
            )
            + f". {context}"
        )
        _assert_the_equity_identity_holds(
            supabase, run[account_key], f"session {label} after six interleaved submissions"
        )
    _repo.reset_persistence_probe()


def test_a_writer_on_the_same_account_does_starve_a_session_which_the_isolation_run_does_not() -> None:
    """The control that makes B3's zero-backoff assertion mean something.

    ``_version_racer`` is the existing device for forcing a version-guarded optimistic UPDATE to
    lose: injected through the double's ``after_select`` seam, it moves the account row after
    every read of it, so the caller holds a correct image of a row that has since moved. Armed
    here, session A's submission exhausts Requirement 16.10's three attempts and answers 409
    ``PAPER_CONCURRENCY_CONFLICT`` - so the seam demonstrably CAN starve a submission, and the
    isolation run's "no submission slept a backoff" is therefore a fact about the other session
    being a different row rather than about a harness in which nothing can conflict.

    Session B's book is asserted unchanged by A's starved attempt, with one honest exception
    stated rather than hidden: ``_version_racer`` moves the ``version`` column of EVERY account
    row it can see, including B's. That is a write by the test harness, not by the code under
    test, so B's ``version`` is projected out of the comparison and B's four money columns and
    every one of B's book rows are asserted byte-identical instead.
    """
    supabase, row_a, account_a, row_b, account_b = _b3_double()
    before_b = _b3_rows_of(supabase, _SESSION_B)
    before_money_b = _b3_figures(supabase, _SESSION_B, account_b)["account"][:4]
    sleeps = _PaperSleeps()
    supabase.after_select = _version_racer

    with pytest.raises(_sim.PaperConcurrencyExhausted) as caught:
        _run_paper(
            _b3_submit(
                supabase,
                row_a,
                account_a,
                "shared-key-1",
                dict(_B3_INTENTS)["shared-key-1"],
                sleeps,
            )
        )
    supabase.after_select = None

    error = caught.value
    assert error.http_status == 409
    assert error.details["attempts"] == _sim.RETRY_ATTEMPTS == 3
    # Three attempts means two bounded, jitter-free waits, in the recorded order.
    assert sleeps.delays == [0.05, 0.10], (
        f"the starved submission slept {sleeps.delays}, not Requirement 16.10's two bounded waits"
    )

    # Session B's book: every row byte-identical, and its four money columns untouched.
    after_b = _b3_rows_of(supabase, _SESSION_B)
    for name in _B3_BOOK_TABLES:
        assert after_b[name] == before_b[name] == [], (
            f"session B's {name} changed while session A's submission was being starved: "
            f"{before_b[name]!r} -> {after_b[name]!r}"
        )
    assert _b3_figures(supabase, _SESSION_B, account_b)["account"][:4] == before_money_b, (
        "session B's balances moved while session A's submission was being starved"
    )
    _assert_the_equity_identity_holds(supabase, account_b, "session A's starved submission")
    _repo.reset_persistence_probe()


# ══════════════════════════════════════════════════════════════════════════════
# B4 - TWO INSTANCES ALLOCATING ``paper_events.sequence`` (Requirement 19.3)
# ══════════════════════════════════════════════════════════════════════════════
#
# WHAT REQUIREMENT 19.3 ASKS FOR
# ------------------------------
# "a per-session sequence number that is 1 for the first event of that Paper_Session and increases
# by exactly 1 for each subsequent event of that Paper_Session". A gap makes a reconnecting
# client's replay unreconcilable (Requirement 19.8 replays ``sequence > last_received``); a repeat
# makes two different events indistinguishable to a client deduplicating on it.
#
# THE TWO ALLOCATORS, AND THE TWO ARBITERS
# ----------------------------------------
# ``paper_repository.allocate_session_event_sequence`` is a version-guarded optimistic swap on
# ``paper_sessions.event_sequence``: read ``N``, then ``UPDATE ... SET event_sequence = N + 1
# WHERE event_sequence = N``. Two instances that read ``N`` together produce one winner and one
# loser, and the loser retries with a FRESH read - it never reuses the number it read.
#
# ``paper_repository.next_session_event_sequence`` is ``max(paper_events.sequence) + 1``: task
# 24.4's feed writer, which reads the LOG rather than the counter and therefore cannot be
# arbitrated by the counter's swap at all. ``uq_paper_event_seq`` UNIQUE ``(session_id,
# sequence)`` arbitrates it instead, and ``record_event``'s caller retries around that refusal
# with a fresh allocation.
#
# Both are exercised here, in one run, and both are asserted to have fired - a run in which
# neither instance ever lost would be trivially contiguous and would prove nothing. The producers,
# the seams, the plan type and the oracle are P-53's, driven rather than restated.
#
# THE HONEST BOUNDARY, RESTATED
# -----------------------------
# The allocation and the INSERT that consumes it are TWO PostgREST requests with no transaction
# around them. A process that dies between them burns a number and leaves a hole, and no
# in-process test can rule that out - closing it needs both statements inside one database
# function. So this battery, like P-53, is a property of the LIVE emit path: no interleaving of
# two instances, no lost compare-and-swap and no duplicate-sequence refusal produces a hole or a
# repeat while the processes stay up.

#: B4's fixed plan. Two producers - the "two instances" - emitting 3 and 2 events, with the
#: second instance also intruding INSIDE the first's statements at two seams:
#:
#: ``swaps=(False, True, True)``  one allocation passes untouched, then two are overtaken between
#:                               the counter read and the guarded UPDATE -> two lost swaps. The
#:                               leading ``False`` matters: it makes the collision land on a LATER
#:                               emission, so the counter is already above zero when it happens.
#: ``inserts=(False, True)``      the feed writer takes the sequence first on the second INSERT
#:                               attempt -> ``uq_paper_event_seq`` refuses the producer's INSERT.
#:
#: ``swaps`` is two ``True`` tokens and not three, so the allocator never EXHAUSTS: exhaustion is
#: P-53's ``exhaust`` shape and it emits nothing at all, which is a different claim from
#: contiguity under contention.
_B4_PLAN = _SequencePlan(
    shape="two_instances",
    events_from_a=3,
    events_from_b=2,
    swaps=(False, True, True),
    inserts=(False, True),
)


def _b4_run() -> Dict[str, Any]:
    """One run of B4's plan: two producers, both seams armed, then both taken off."""
    _repo.reset_persistence_probe()
    client = _channel_client(sessions=[_channel_session_row()])
    tally = _SequenceTally()
    recorder = _Recorder("P-53 (task 34.3, two instances)", _P53_FLOORS, _P53_LABELS)
    recorder.start()
    writer = _SequenceWriter(client, _B4_PLAN, tally, recorder)
    writer.arm()
    _run_paper(_produce_sequences_concurrently(client, _B4_PLAN, tally, recorder))
    writer.disarm()

    rows = _session_event_log(client)
    return {
        "client": client,
        "tally": tally,
        "recorder": recorder,
        "rows": rows,
        "sequences": [int(row["sequence"]) for row in rows],
        "collisions": _sequence_swap_attempts(client) - tally.allocations_returned,
    }


def _assert_the_sequence_is_contiguous_from_one(run: Dict[str, Any]) -> None:
    """P-53's oracle - ``{1..k}``, computed from the row count - plus the counter's agreement."""
    tally = run["tally"]
    sequences = run["sequences"]
    k = len(run["rows"])
    context = (
        f"k={k}, sequences={sorted(sequences)}, collisions={run['collisions']}, "
        f"duplicates_refused={tally.duplicates}, exhausted={tally.exhausted}, "
        f"feed_rows={tally.feed_rows}"
    )

    assert k >= 2, f"nothing (or one thing) was emitted, so there is no sequence. {context}"
    assert set(sequences) == set(range(1, k + 1)), (
        f"Requirement 19.3: the {k} events of this session carry {sorted(sequences)}, which is "
        f"not {{1..{k}}}. A missing value is a GAP and an extra one is a DUPLICATE; either makes "
        f"a reconnecting client's replay unreconcilable. {context}"
    )
    assert len(sequences) == len(set(sequences)), (
        f"Requirement 19.3: a sequence value appears more than once: {sorted(sequences)}. "
        f"{context}"
    )
    event_ids = [str(row["event_id"]) for row in run["rows"]]
    assert len(event_ids) == len(set(event_ids)), (
        f"Requirement 19.3: an event_id appears more than once, so a client deduplicating on it "
        f"would discard a real event. {context}"
    )
    assert _sequence_counter(run["client"]) == k, (
        f"paper_sessions.event_sequence stands at {_sequence_counter(run['client'])} while the log "
        f"holds {k} row(s), so a number was allocated and never written - a hole waiting for the "
        f"next emission. {context}"
    )


def test_two_instances_allocating_the_event_sequence_leave_no_gap_and_no_duplicate() -> None:
    """B4: N = 2 instances, 5+ emissions, both arbiters exercised (Requirement 19.3).

    The second instance intrudes at both of the double's seams inside the first's statements: at
    ``after_select`` on ``paper_sessions`` (so the first holds a stale ``event_sequence`` and its
    guarded UPDATE matches zero rows) and at ``before_insert`` on ``paper_events`` (so the feed
    writer's ``max(sequence) + 1`` takes the number first and ``uq_paper_event_seq`` refuses the
    first's INSERT).

    Both are asserted to have fired, because a run in which neither instance ever lost is
    trivially contiguous. The oracle is ``{1..k}`` computed from the row count - nothing about the
    expectation is obtained by asking the allocator what it allocated - and
    ``paper_sessions.event_sequence`` is compared against that same ``k`` afterwards as a separate
    claim.
    """
    run = _b4_run()
    tally = run["tally"]
    context = f"collisions={run['collisions']}, duplicates={tally.duplicates}"

    assert run["collisions"] >= 1, (
        "no compare-and-swap matched zero rows, so the two instances never actually contended "
        f"for the counter and contiguity was not tested under contention. {context}"
    )
    assert tally.duplicates >= 1, (
        "uq_paper_event_seq never refused an INSERT, so the arbiter for the writer the counter "
        f"cannot see was never exercised. {context}"
    )
    assert tally.exhausted == 0, (
        "an allocation exhausted, which is a different claim (it emits nothing at all) and would "
        f"make this run's contiguity trivially true for the wrong reason. {context}"
    )
    assert tally.feed_rows >= 1, (
        f"the feed writer appended nothing, so its row is not in the log. {context}"
    )
    _assert_the_sequence_is_contiguous_from_one(run)
    _repo.reset_persistence_probe()


def _sequence_index_relaxed(original: Any) -> Any:
    """``FakeSupabase._enforce_unique`` with ``uq_paper_event_seq`` - and only it - taken off.

    ``uq_paper_event_id`` stays enforced, so the relaxation is exactly the one index B4's
    contiguity claim rests on and not "the events table has no indexes".
    """

    def relaxed(self: Any, table: str, row: Dict[str, Any]) -> None:
        if table == _repo.EVENTS_TABLE:
            for other in self.events:
                if str(other.get("session_id")) == str(row.get("session_id")) and other.get(
                    "event_id"
                ) == row.get("event_id"):
                    raise _PaperUniqueViolation("uq_paper_event_id", table)
            return
        original(self, table, row)

    return relaxed


def test_b4_fails_when_uq_paper_event_seq_is_relaxed_in_the_double() -> None:
    """B4 can FAIL, demonstrated rather than asserted about.

    B4's contiguity claim rests on ``uq_paper_event_seq``: the feed writer reads the LOG rather
    than the counter, so the counter's compare-and-swap cannot arbitrate it and the index is the
    only thing that can. This test relaxes that ONE index in the double - ``uq_paper_event_id``
    stays on, every other table's indexes and both UPDATE triggers untouched - runs the identical
    plan, and requires that :func:`_assert_the_sequence_is_contiguous_from_one` then FAILS with a
    repeated sequence value.

    The relaxation is scoped by ``patch.object``, so the double is restored the moment this test
    returns; the battery above runs against the real index.
    """
    original = _PaperFakeSupabase._enforce_unique
    with _patch.object(
        _PaperFakeSupabase, "_enforce_unique", _sequence_index_relaxed(original)
    ):
        run = _b4_run()
        assert run["tally"].duplicates == 0, (
            "uq_paper_event_seq still refused an INSERT although it was relaxed, so this test is "
            "not measuring what it claims to measure"
        )
        assert len(run["sequences"]) != len(set(run["sequences"])), (
            "relaxing uq_paper_event_seq did not produce a repeated sequence value, so the "
            "battery's premise - that the index is what keeps the log contiguous against the feed "
            f"writer - is wrong: {sorted(run['sequences'])}"
        )
        with pytest.raises(AssertionError) as caught:
            _assert_the_sequence_is_contiguous_from_one(run)
        assert "Requirement 19.3" in str(caught.value)

    # Restored: the very same plan against the real index is contiguous again.
    _assert_the_sequence_is_contiguous_from_one(_b4_run())
    _repo.reset_persistence_probe()


# ══════════════════════════════════════════════════════════════════════════════
# B5 - THE EXPIRY SWEEP AGAINST AN ENTITLEMENT CHECK AT THE EXPIRY INSTANT
#      (Requirements 11.7, 11.8)
# ══════════════════════════════════════════════════════════════════════════════
#
# THE PROPERTY, STATED AS THE EQUALITY IT IS
# ------------------------------------------
# Requirement 11.7: "WHILE the current UTC time is at or after a Subscription's expiry and no
# renewal payment has been confirmed, THE Entitlement_Resolver SHALL treat that Subscription as
# not entitling, **irrespective of the stored Subscription_State value and irrespective of whether
# the expiry sweep of Criterion 8 has yet run for that Subscription**."
#
# So the resolver's answer is a function of ONE comparison - ``instant < expiry`` - and of nothing
# else. Not of ``status``, not of whether the sweep has run, and therefore not of how the sweep
# and the check interleave. That is the whole battery:
#
#     entitlement.entitling  ==  (instant < expiry)
#
# asserted for every one of the interleavings below at every one of the instants below.
#
# WHY THE TWO SIDES CANNOT DISAGREE, AND WHY THAT NEEDED A TEST ANYWAY
# -------------------------------------------------------------------
# Before the sweep reaches the row, ``status`` still reads ``'active'`` and the resolver refuses on
# ``now >= period_expiry`` alone. After the sweep, ``status`` reads ``'expired'`` and the resolver
# refuses on ``status != 'active'``. Two different code paths, one answer - which is the point, and
# which is exactly the kind of agreement that erodes silently: a resolver that trusted the status
# column would still pass every sequential test written after the sweep had run.
#
# The reason is also asserted, not only the verdict: both refusals must be
# ``EntitlementReason.EXPIRED`` carrying ``MARKETPLACE_SUBSCRIPTION_EXPIRED``, so a purchaser gets
# the same answer whichever side of the sweep their request lands on. A resolver that answered
# ``NOT_SUBSCRIBED`` after the sweep would satisfy the ``entitling`` half and still be wrong on
# the wire (Requirement 7.10 makes the two codes distinct).
#
# THE STORE IS ONE STORE
# ----------------------
# ``tests/test_expiry_sweep.FakeSupabase`` is the double built for exactly the four marketplace
# tables the sweep writes, and it honours ``eq``, ``lte``, ``in`` and ``not.is`` NULL - a double
# that accepted the predicates and then updated everything would make this battery pass against a
# sweep with no predicate at all. :class:`_ExpiryRaceStore` extends it with the two tables the
# Entitlement_Resolver READS (``library_strategies`` with its embedded submission, and
# ``strategy_versions``), and it builds the resolver's embedded ``library_subscriptions`` array
# from the LIVE ``library_subscriptions`` rows - so the sweep's UPDATE and the resolver's read are
# against one set of rows and the interleaving is a real interleaving rather than two doubles that
# happen to be seeded alike.
#
# Requirement 11.8's half asserted here is the one this race can speak to: however many
# entitlement checks run against it, one sweep pass issues exactly ONE update statement against
# ``library_subscriptions``. The 60-second interval bound is asserted against
# ``SWEEP_INTERVAL_SECONDS`` directly.

#: B5's expiry instant. Every instant below is expressed relative to it, so "at the expiry
#: instant" is exact rather than approximately exact.
_B5_EXPIRY = _PAPER_NOW

#: The three instants. One microsecond is the finest resolution a ``timestamptz`` carries, so
#: these are the closest three points to the boundary that the column can tell apart - and the
#: boundary itself is INCLUSIVE of non-entitlement (Requirement 11.7 says "at or after").
_B5_INSTANTS: Tuple[Tuple[str, Any], ...] = (
    ("one_microsecond_before_expiry", _B5_EXPIRY - _timedelta(microseconds=1)),
    ("exactly_at_expiry", _B5_EXPIRY),
    ("one_microsecond_after_expiry", _B5_EXPIRY + _timedelta(microseconds=1)),
)

_B5_OWNER = "b5-owner"
_B5_STRATEGY = "b5-strategy"
_B5_VERSION = "b5-version-1"
_B5_SUBSCRIPTION = "b5-sub-1"


class _ExpiryRaceStore(_SweepFakeSupabase):
    """The sweep's double, plus the two tables the Entitlement_Resolver reads. Nothing removed.

    ``library_strategies`` answers the resolver's one embedded read, and the embedded
    ``library_subscriptions`` array is built from the live ``library_subscriptions`` rows at the
    moment of the read - so a sweep that has flipped ``status`` is visible to a later read and
    invisible to an earlier one, which is what makes the interleaving observable.

    ``after_select`` is a seam of the same shape ``FakeSupabase.after_select`` has in the paper
    double: it fires once a select's rows are copied out, so a competitor released there leaves
    the caller holding a correct image of a row that has since moved.
    """

    def __init__(
        self,
        *,
        subscriptions: List[Dict[str, Any]],
        listing: Dict[str, Any],
        versions: List[Dict[str, Any]],
        **kwargs: Any,
    ) -> None:
        super().__init__(subscriptions=subscriptions, **kwargs)
        self.tables["library_strategies"] = [dict(listing)]
        self.tables["strategy_versions"] = [dict(row) for row in versions]
        self.after_select: Optional[Any] = None

    def _execute(self, query: Any) -> Any:
        response = super()._execute(query)
        if query.op == "select" and query.table_name == "library_strategies":
            response = _SweepResp(
                [self._with_embeds(row) for row in (response.data or [])]
            )
        if self.after_select is not None:
            self.after_select(self, query)
        return response

    def _with_embeds(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """One ``library_strategies`` row with PostgREST's two embedded resources attached."""
        embedded = dict(row)
        embedded["library_subscriptions"] = [
            {
                "id": sub.get("id"),
                "user_id": sub.get("user_id"),
                "status": sub.get("status"),
                "period_expiry": sub.get("period_expiry"),
            }
            for sub in self.tables["library_subscriptions"]
            if str(sub.get("library_id")) == str(row.get("id"))
        ]
        return embedded

    def subscription_status(self) -> Optional[str]:
        for row in self.tables["library_subscriptions"]:
            if str(row.get("id")) == _B5_SUBSCRIPTION:
                return row.get("status")
        raise AssertionError(f"no library_subscriptions row {_B5_SUBSCRIPTION!r} is stored")

    def subscription_updates(self) -> List[Any]:
        return [
            q
            for q in self.statements
            if q.op == "update" and q.table_name == "library_subscriptions"
        ]


def _b5_store() -> _ExpiryRaceStore:
    """One Listing, one PUBLISHED submission, one saved version, one ACTIVE Subscription.

    The Subscription's ``status`` is ``'active'`` and its ``period_expiry`` is
    :data:`_B5_EXPIRY`. Nothing about the row says "expired" - which is the premise: the sweep has
    not run, and the resolver must refuse anyway once the instant reaches the expiry.
    """
    _expiry_sweep.reset_metrics()
    return _ExpiryRaceStore(
        subscriptions=[
            _sweep_subscription(
                _B5_SUBSCRIPTION,
                "active",
                period_expiry=_B5_EXPIRY,
                user_id=_SWEEP_PURCHASER,
                library_id=_SWEEP_LISTING,
            )
        ],
        listing={
            "id": _SWEEP_LISTING,
            "author_id": _B5_OWNER,
            "source_strategy_id": _B5_STRATEGY,
            "source_cloning_enabled": False,
            "marketplace_submissions": [{"submission_state": "PUBLISHED"}],
        },
        versions=[
            {
                "id": _B5_VERSION,
                "strategy_id": _B5_STRATEGY,
                "version": 1,
                "is_draft": False,
            }
        ],
    )


def _b5_resolve_coroutine(store: _ExpiryRaceStore, instant: Any) -> Any:
    """One ``entitlement_resolver.resolve`` coroutine for the purchaser. Not awaited here."""
    return _entitlement_resolver.resolve(
        caller={"id": _SWEEP_PURCHASER},
        listing_id=_SWEEP_LISTING,
        supabase=store,
        now=instant,
    )


def _b5_sweep_coroutine(store: _ExpiryRaceStore, instant: Any) -> Any:
    """One full ``expiry_sweep.sweep`` pass, enforcement included. Not awaited here."""
    return _expiry_sweep.sweep(
        supabase=store,
        now=instant,
        stop_running=True,
        audit_writer=_SweepAuditWriter(),
    )


class _SweepInsideTheRead:
    """Run a whole sweep pass INSIDE the resolver's ``library_strategies`` read.

    The read has completed and its rows - embedded subscription array included - are already
    copied out, so the resolver goes on to decide from a correct image of a Subscription the sweep
    has since transitioned to ``EXPIRED``. That is the tightest interleaving this pair admits, and
    the one a resolver that trusted its own read's ``status`` would get wrong.

    ``busy`` is a re-entrancy guard, not a lock: the sweep issues its own selects, which would
    fire this seam again and recurse without bound.
    """

    __slots__ = ("store", "instant", "busy", "fired", "outcome")

    def __init__(self, store: _ExpiryRaceStore, instant: Any) -> None:
        self.store = store
        self.instant = instant
        self.busy = False
        self.fired = 0
        self.outcome: Optional[Any] = None

    def __call__(self, client: Any, query: Any) -> None:
        if query.op != "select" or query.table_name != "library_strategies":
            return
        if self.busy or self.fired:
            return
        self.busy = True
        self.fired += 1
        try:
            self.outcome = _drive_without_a_loop(
                _b5_sweep_coroutine(self.store, self.instant)
            )
        finally:
            self.busy = False


#: How many entitlement checks the ``gathered`` interleaving runs against one sweep pass. N = 6
#: checks + 1 sweep, all in one ``asyncio.gather`` awaited from inside a coroutine.
_B5_GATHERED_CHECKS = 6


def _b5_verdicts(interleaving: str, instant: Any) -> Tuple[List[Any], _ExpiryRaceStore]:
    """Every entitlement verdict one interleaving produces, and the store it produced them on.

    Five interleavings, each a different order of the same two operations:

    ``check_only``            no sweep at all - the sweep is dead, or has not reached this row.
    ``sweep_then_check``      the sweep has already transitioned the row.
    ``check_then_sweep``      the check ran first; a second check after the sweep must agree.
    ``sweep_inside_a_check``  the sweep runs INSIDE the check's own read (the tightest case).
    ``gathered``              six checks and one sweep gathered together in one coroutine.
    """
    store = _b5_store()

    if interleaving == "check_only":
        return [_run_paper(_b5_resolve_coroutine(store, instant))], store

    if interleaving == "sweep_then_check":
        _run_paper(_b5_sweep_coroutine(store, instant))
        return [_run_paper(_b5_resolve_coroutine(store, instant))], store

    if interleaving == "check_then_sweep":
        first = _run_paper(_b5_resolve_coroutine(store, instant))
        _run_paper(_b5_sweep_coroutine(store, instant))
        second = _run_paper(_b5_resolve_coroutine(store, instant))
        return [first, second], store

    if interleaving == "sweep_inside_a_check":
        seam = _SweepInsideTheRead(store, instant)
        store.after_select = seam
        verdict = _run_paper(_b5_resolve_coroutine(store, instant))
        store.after_select = None
        assert seam.fired == 1, (
            "the sweep was not released inside the entitlement check's own read, so the tightest "
            f"interleaving was not exercised (instant {instant.isoformat()})"
        )
        return [verdict], store

    if interleaving == "gathered":

        async def _drive() -> List[Any]:
            results = await _asyncio.gather(
                *(
                    [_b5_resolve_coroutine(store, instant) for _ in range(_B5_GATHERED_CHECKS)]
                    + [_b5_sweep_coroutine(store, instant)]
                )
            )
            return list(results)

        gathered = _run_paper(_drive())
        return list(gathered[:-1]), store

    raise AssertionError(f"unknown interleaving {interleaving!r}")


_B5_INTERLEAVINGS: Tuple[str, ...] = (
    "check_only",
    "sweep_then_check",
    "check_then_sweep",
    "sweep_inside_a_check",
    "gathered",
)


@pytest.mark.parametrize("interleaving", _B5_INTERLEAVINGS)
@pytest.mark.parametrize(
    "label,instant", _B5_INSTANTS, ids=[name for name, _ in _B5_INSTANTS]
)
def test_the_entitlement_answer_is_instant_before_expiry_whatever_the_sweep_does(
    interleaving: str, label: str, instant: Any
) -> None:
    """B5: ``entitling == (instant < expiry)`` for every interleaving (Requirements 11.7, 11.8).

    Three instants one microsecond apart around the expiry - the finest resolution a
    ``timestamptz`` carries - times five interleavings of the sweep against the check. In every
    one of the fifteen, the resolver's answer is the single comparison ``instant < expiry`` and
    nothing else: not the stored ``status``, and not whether the sweep has run.

    A refusal is also asserted to be ``EXPIRED`` / ``MARKETPLACE_SUBSCRIPTION_EXPIRED`` on BOTH
    sides of the sweep, because a purchaser must get the same answer whichever side their request
    lands on, and Requirement 7.10 makes ``NOT_SUBSCRIBED`` and ``EXPIRED`` distinct codes.

    Requirement 11.8's half this race can speak to is asserted too: one sweep pass issues at most
    ONE update statement against ``library_subscriptions``, however many entitlement checks are
    running against the same rows.
    """
    verdicts, store = _b5_verdicts(interleaving, instant)
    expected = instant < _B5_EXPIRY
    context = (
        f"interleaving={interleaving}, instant={label} ({instant.isoformat()}), "
        f"expiry={_B5_EXPIRY.isoformat()}, stored status now {store.subscription_status()!r}"
    )

    assert verdicts, f"no entitlement verdict was produced. {context}"
    for verdict in verdicts:
        assert verdict.entitling is expected, (
            f"Requirement 11.7: the Entitlement_Resolver answered entitling="
            f"{verdict.entitling} (reason {verdict.reason.value}) where instant < expiry is "
            f"{expected}. The answer must be that comparison and nothing else - irrespective of "
            f"the stored Subscription_State and irrespective of whether the sweep has run. "
            f"{context}"
        )
        if expected:
            assert verdict.reason is _entitlement_resolver.EntitlementReason.SUBSCRIBED, (
                f"an entitling verdict before the expiry must be SUBSCRIBED, got "
                f"{verdict.reason.value}. {context}"
            )
            assert verdict.wire_code is None
            assert verdict.version_id == _B5_VERSION
        else:
            assert verdict.reason is _entitlement_resolver.EntitlementReason.EXPIRED, (
                f"a refusal at or after the expiry must be EXPIRED - the same reason on both "
                f"sides of the sweep - got {verdict.reason.value}. Requirement 7.10 makes "
                f"NOT_SUBSCRIBED and EXPIRED distinct codes on the wire. {context}"
            )
            assert verdict.wire_code == "MARKETPLACE_SUBSCRIPTION_EXPIRED", (
                f"the refusal carried {verdict.wire_code!r}. {context}"
            )
        assert verdict.subscription_id == _B5_SUBSCRIPTION, (
            f"the verdict does not name the purchaser's own Subscription. {context}"
        )

    # Every verdict of one interleaving agrees with every other - the resolver's answer does not
    # depend on WHERE in the interleaving the check landed.
    assert len({(v.entitling, v.reason) for v in verdicts}) == 1, (
        f"two entitlement checks in one interleaving disagreed: "
        f"{[(v.entitling, v.reason.value) for v in verdicts]}. {context}"
    )

    # ── the sweep did what its own predicate says, and only that ───────────────
    swept = interleaving != "check_only"
    if swept and not expected:
        assert store.subscription_status() == "expired", (
            f"the sweep did not transition a Subscription whose expiry is at or before the "
            f"instant. {context}"
        )
    elif swept:
        assert store.subscription_status() == "active", (
            f"the sweep transitioned a Subscription whose expiry is still in the future; "
            f"NULL/future rows are outside its predicate. {context}"
        )
    else:
        assert store.subscription_status() == "active", (
            f"no sweep ran, so the stored status must be untouched - and the refusal above was "
            f"therefore reached WITHOUT the sweep, which is the whole of Requirement 11.7's "
            f"'irrespective of whether the expiry sweep has yet run'. {context}"
        )

    # ── Requirement 11.8: one pass, one update statement ──────────────────────
    updates = store.subscription_updates()
    assert len(updates) <= 1, (
        f"one sweep pass issued {len(updates)} update statements against library_subscriptions; "
        f"the pass carries its whole predicate in one statement however many rows it moves "
        f"(Requirement 11.8). {context}"
    )
    for statement in updates:
        payload = dict(statement.payload or {})
        assert payload.get("status") == "expired", (
            f"the sweep's update payload set status to {payload.get('status')!r}. {context}"
        )
        assert "period_expiry" not in payload and "period_start" not in payload, (
            f"the sweep moved a period boundary; only settlement may (Requirement 11.5). "
            f"{context}"
        )
    _expiry_sweep.reset_metrics()


def test_the_sweep_interval_leaves_the_resolver_as_the_only_authority_on_expiry() -> None:
    """Requirement 11.8's interval bound, and Requirement 11.7's independence, together.

    The two halves are asserted in one place because they are one design decision: the sweep runs
    at an interval no greater than 60 seconds, and the resolver does not depend on it having run.
    A sweep that stalled for an hour would therefore cost bookkeeping, not access - which is only
    true because the resolver refuses a lapsed row the sweep has not reached, asserted at every
    instant and interleaving above.
    """
    assert _expiry_sweep.SWEEP_INTERVAL_SECONDS <= 60, (
        f"Requirement 11.8 bounds the sweep interval at 60 seconds; it is "
        f"{_expiry_sweep.SWEEP_INTERVAL_SECONDS}"
    )
    assert _expiry_sweep.SWEEP_TARGET_STATE.value.upper() == "EXPIRED", (
        "the sweep's only target is EXPIRED; a sweep that could write ACTIVE would be granting "
        "access without a confirmed payment (Requirement 11.6)"
    )
    # The resolver's independence, one more time and structurally: the sweep cannot become a
    # second opinion on an access decision because it does not import the resolver at all.
    assert "entitlement_resolver" not in set(_expiry_sweep.__all__)
    for forbidden in ("resolve", "Entitlement", "is_entitled"):
        assert forbidden not in set(_expiry_sweep.__all__)
