"""
tests/test_submission_service.py

Focused unit tests for ``backend_app/backend/marketplace/submission_service.py`` (task 14.2).

These exercise the service against a FAKE Supabase double that records the ``.table(...)``
chains it received and returns scripted rows, so the transaction shape, the compensation on
failure, the ``23505`` translation, the reason rule, the admin-action atomicity and the
evidence-only admin detail are all checkable without a database or a TestClient - the same way
``eligibility_gate`` is designed to be tested (the injected ``supabase`` handle is a parameter).

What is asserted
----------------
* Purity: the module imports no FastAPI, at the source level (AST) and at import time.
* ``create_submission`` writes the parent Submission, one evidence row per condition carrying
  ``source_backtest_id`` and the copied parameters/metrics, then advances DRAFT -> SUBMITTED and
  appends a transition row - all before returning the SUBMITTED row.
* An evidence-insert failure compensates (deletes evidence + parent) and raises
  ``MARKETPLACE_EVIDENCE_PERSIST_FAILED`` - no partial state.
* A ``23505`` on ``uq_submission_open_per_strategy`` at the SUBMITTED update maps to
  ``MARKETPLACE_SUBMISSION_ALREADY_OPEN`` and compensates.
* ``validate_reason`` enforces 1..2000 trimmed chars, raising ``MARKETPLACE_REASON_REQUIRED``.
* ``apply_admin_action`` refuses an illegal edge (``MARKETPLACE_SUBMISSION_TRANSITION_REJECTED``)
  and a missing row (``MARKETPLACE_SUBMISSION_NOT_FOUND``), and rolls the state back to
  ``MARKETPLACE_ACTION_NOT_RECORDED`` when the audit write raises.
* ``admin_detail`` reads ``marketplace_backtest_evidence`` and never a ``*.blueprint`` /
  ``strategies`` logic column; a mutation of persisted evidence yields
  ``MARKETPLACE_EVIDENCE_IMMUTABLE``.
"""

from __future__ import annotations

import ast
import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend.marketplace import submission_service as svc
from backend_app.backend.marketplace.submission_service import (
    MARKETPLACE_ACTION_NOT_RECORDED,
    MARKETPLACE_EVIDENCE_IMMUTABLE,
    MARKETPLACE_EVIDENCE_PERSIST_FAILED,
    MARKETPLACE_REASON_REQUIRED,
    MARKETPLACE_SUBMISSION_ALREADY_OPEN,
    MARKETPLACE_SUBMISSION_NOT_FOUND,
    MARKETPLACE_SUBMISSION_TRANSITION_REJECTED,
    SubmissionAction,
    SubmissionServiceError,
    admin_detail,
    apply_admin_action,
    create_submission,
    refuse_evidence_mutation,
    validate_reason,
)

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "backend_app"
    / "backend"
    / "marketplace"
    / "submission_service.py"
)


# ══════════════════════════════════════════════════════════════════════════
# A fake Supabase double
# ══════════════════════════════════════════════════════════════════════════


class _Resp:
    def __init__(self, data):
        self.data = data


class _Query:
    """A recording query builder that mimics the supabase-py fluent chain."""

    def __init__(self, table: str, client: "FakeSupabase", op: str):
        self.table_name = table
        self.client = client
        self.op = op
        self.payload: Any = None
        self.filters: List[tuple] = []
        self._order: Optional[str] = None

    # writes
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

    def select(self, cols):
        self.op = "select"
        self.payload = cols
        return self

    # filters
    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def order(self, col, *a, **k):
        self._order = col
        return self

    def execute(self):
        return self.client._execute(self)


class FakeSupabase:
    """Records operations and returns scripted results.

    ``script`` maps an ``(table, op)`` key to a callable ``(query) -> rows`` or to a fixed rows
    list. ``raise_on`` maps an ``(table, op)`` key to an exception to raise. ``calls`` records
    every executed query for assertions.
    """

    def __init__(self, script=None, raise_on=None):
        self.script = script or {}
        self.raise_on = raise_on or {}
        self.calls: List[_Query] = []
        self.rows: Dict[str, List[dict]] = {}

    def table(self, name):
        return _Query(name, self, op="")

    def _execute(self, q: _Query):
        self.calls.append(q)
        key = (q.table_name, q.op)
        if key in self.raise_on:
            raise self.raise_on[key]
        if key in self.script:
            val = self.script[key]
            rows = val(q) if callable(val) else val
            return _Resp(rows)
        return _Resp([])

    def ops(self):
        return [(c.table_name, c.op) for c in self.calls]


def _run(coro):
    return asyncio.run(coro)


def _backtest_rows(n=3):
    rows = []
    for i in range(n):
        rows.append(
            {
                "id": f"bt-{i}",
                "version_id": "ver-1",
                "dataset": f"BTCUSDT-{i}",
                "start_date": "2023-01-01",
                "end_date": "2023-06-01",
                "initial_capital": "10000.00",
                "commission": "0.001",
                "slippage": "0.0005",
                "dataset_checksum": f"chk-{i}",
                "dag_hash": "dag-1",
                "engine_version": "engine/1.0",
                "executed_bar_count": 120,
                "total_return_pct": "12.5",
                "sharpe_ratio": "1.4",
                "sortino_ratio": "1.9",
                "max_drawdown": "8.0",
                "win_rate": "55.0",
                "profit_factor": "1.6",
                "total_trades": 40,
                "final_capital": "11250.00",
            }
        )
    return rows


# ══════════════════════════════════════════════════════════════════════════
# Purity
# ══════════════════════════════════════════════════════════════════════════


def test_module_imports_no_fastapi_at_source_level():
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    assert "fastapi" not in roots
    # errors.py must not be imported (it imports FastAPI)
    assert "errors" not in {
        n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module
        for _ in [0] if n.module.endswith("errors")
    }


def test_module_does_not_load_fastapi_at_import_time():
    # Import the module in a fresh subprocess and confirm fastapi was not pulled in.
    import subprocess

    code = (
        "import sys; import backend_app.backend.marketplace.submission_service as m; "
        "print('fastapi' in sys.modules)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "False", out.stdout


# ══════════════════════════════════════════════════════════════════════════
# create_submission — happy path
# ══════════════════════════════════════════════════════════════════════════


def test_create_submission_writes_submission_evidence_and_transition():
    fake = FakeSupabase(
        script={
            ("marketplace_submissions", "insert"): [
                {"id": "sub-1", "submission_state": "DRAFT"}
            ],
            ("marketplace_submissions", "update"): lambda q: [
                {
                    "id": "sub-1",
                    "submission_state": "SUBMITTED",
                    "source_strategy_id": "strat-1",
                    "version_id": "ver-1",
                    "listing_id": "lst-1",
                }
            ],
        }
    )
    result = _run(
        create_submission(
            caller={"id": "owner-1"},
            listing_id="lst-1",
            source_strategy_id="strat-1",
            version_id="ver-1",
            backtest_rows=_backtest_rows(3),
            eligibility_outcomes=[{"code": "MP_OWNERSHIP", "passed": True}],
            evaluator_version="eligibility-gate/1.0.0",
            supabase=fake,
        )
    )
    assert result["submission_state"] == "SUBMITTED"

    ops = fake.ops()
    assert ("marketplace_submissions", "insert") in ops
    assert ("marketplace_backtest_evidence", "insert") in ops
    assert ("marketplace_submissions", "update") in ops
    assert ("marketplace_submission_transitions", "insert") in ops

    # the evidence insert carries one row per condition with source_backtest_id + copied metrics
    ev_call = next(
        c
        for c in fake.calls
        if c.table_name == "marketplace_backtest_evidence" and c.op == "insert"
    )
    assert isinstance(ev_call.payload, list)
    assert len(ev_call.payload) == 3
    first = ev_call.payload[0]
    assert first["source_backtest_id"] == "bt-0"
    assert first["condition_index"] == 0
    assert first["dataset"] == "BTCUSDT-0"
    # renamed metric columns land under the evidence-table names
    assert first["max_drawdown_pct"] == "8.0"
    assert first["win_rate_pct"] == "55.0"
    assert first["sortino_ratio"] == "1.9"
    assert first["version_id"] == "ver-1"


# ══════════════════════════════════════════════════════════════════════════
# create_submission — failure & compensation
# ══════════════════════════════════════════════════════════════════════════


def test_evidence_insert_failure_compensates_and_raises_persist_failed():
    fake = FakeSupabase(
        script={
            ("marketplace_submissions", "insert"): [{"id": "sub-1"}],
        },
        raise_on={
            ("marketplace_backtest_evidence", "insert"): RuntimeError("boom"),
        },
    )
    with pytest.raises(SubmissionServiceError) as ei:
        _run(
            create_submission(
                caller={"id": "owner-1"},
                listing_id="lst-1",
                source_strategy_id="strat-1",
                version_id="ver-1",
                backtest_rows=_backtest_rows(3),
                eligibility_outcomes=[],
                evaluator_version="v",
                supabase=fake,
            )
        )
    assert ei.value.code == MARKETPLACE_EVIDENCE_PERSIST_FAILED
    # compensation: parent Submission delete was issued (evidence delete skipped, none written)
    assert ("marketplace_submissions", "delete") in fake.ops()


def test_23505_on_open_index_maps_to_already_open_and_compensates():
    class APIError(Exception):
        pass

    err = APIError(
        'duplicate key value violates unique constraint '
        '"uq_submission_open_per_strategy" (SQLSTATE 23505)'
    )
    fake = FakeSupabase(
        script={
            ("marketplace_submissions", "insert"): [{"id": "sub-1"}],
            ("marketplace_backtest_evidence", "insert"): [],
        },
        raise_on={
            ("marketplace_submissions", "update"): err,
        },
    )
    with pytest.raises(SubmissionServiceError) as ei:
        _run(
            create_submission(
                caller={"id": "owner-1"},
                listing_id="lst-1",
                source_strategy_id="strat-1",
                version_id="ver-1",
                backtest_rows=_backtest_rows(3),
                eligibility_outcomes=[],
                evaluator_version="v",
                supabase=fake,
            )
        )
    assert ei.value.code == MARKETPLACE_SUBMISSION_ALREADY_OPEN
    assert ei.value.http_status == 409
    assert ("marketplace_backtest_evidence", "delete") in fake.ops()
    assert ("marketplace_submissions", "delete") in fake.ops()


def test_23505_on_other_constraint_is_persist_failed_not_already_open():
    class APIError(Exception):
        pass

    err = APIError(
        'duplicate key value violates unique constraint '
        '"uq_evidence_submission_checksum" (SQLSTATE 23505)'
    )
    fake = FakeSupabase(
        script={("marketplace_submissions", "insert"): [{"id": "sub-1"}]},
        raise_on={("marketplace_backtest_evidence", "insert"): err},
    )
    with pytest.raises(SubmissionServiceError) as ei:
        _run(
            create_submission(
                caller={"id": "owner-1"},
                listing_id="lst-1",
                source_strategy_id="strat-1",
                version_id="ver-1",
                backtest_rows=_backtest_rows(3),
                eligibility_outcomes=[],
                evaluator_version="v",
                supabase=fake,
            )
        )
    assert ei.value.code == MARKETPLACE_EVIDENCE_PERSIST_FAILED


# ══════════════════════════════════════════════════════════════════════════
# validate_reason
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("bad", [None, "", "   ", 123, "x" * 2001])
def test_validate_reason_rejects(bad):
    with pytest.raises(SubmissionServiceError) as ei:
        validate_reason(bad)
    assert ei.value.code == MARKETPLACE_REASON_REQUIRED


def test_validate_reason_trims_and_accepts():
    assert validate_reason("  needs work  ") == "needs work"
    assert validate_reason("x" * 2000) == "x" * 2000


# ══════════════════════════════════════════════════════════════════════════
# apply_admin_action
# ══════════════════════════════════════════════════════════════════════════


def _admin_fake(current_state, *, update_ok=True, audit_error=None):
    def update_result(q):
        if not update_ok:
            return []
        return [
            {
                "id": "sub-1",
                "submission_state": q.payload.get("submission_state"),
                "source_strategy_id": "strat-1",
                "version_id": "ver-1",
                "listing_id": "lst-1",
            }
        ]

    return FakeSupabase(
        script={
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
            ("marketplace_submissions", "update"): update_result,
        }
    )


def test_apply_admin_action_missing_submission_is_not_found():
    fake = FakeSupabase(script={("marketplace_submissions", "select"): []})
    with pytest.raises(SubmissionServiceError) as ei:
        _run(
            apply_admin_action(
                {"id": "admin-1"}, "sub-1", SubmissionAction.APPROVE, supabase=fake
            )
        )
    assert ei.value.code == MARKETPLACE_SUBMISSION_NOT_FOUND


def test_apply_admin_action_illegal_edge_is_rejected():
    # publish from DRAFT is not a legal edge
    fake = _admin_fake("DRAFT")
    with pytest.raises(SubmissionServiceError) as ei:
        _run(
            apply_admin_action(
                {"id": "admin-1"}, "sub-1", SubmissionAction.PUBLISH, supabase=fake
            )
        )
    assert ei.value.code == MARKETPLACE_SUBMISSION_TRANSITION_REJECTED
    assert ei.value.details.get("current") == "DRAFT"
    assert ei.value.details.get("rejected") == "PUBLISHED"


def test_apply_admin_action_reject_without_reason_is_reason_required():
    fake = _admin_fake("UNDER_REVIEW")
    with pytest.raises(SubmissionServiceError) as ei:
        _run(
            apply_admin_action(
                {"id": "admin-1"}, "sub-1", SubmissionAction.REJECT, "  ", supabase=fake
            )
        )
    assert ei.value.code == MARKETPLACE_REASON_REQUIRED
    # no write happened - only the FOR-UPDATE read
    assert ("marketplace_submissions", "update") not in fake.ops()


def test_apply_admin_action_approve_from_submitted_walks_two_edges(monkeypatch):
    fake = _admin_fake("SUBMITTED")

    async def _ok(*a, **k):
        return None

    monkeypatch.setattr(svc, "_record_admin_action", _ok)
    result = _run(
        apply_admin_action(
            {"id": "admin-1"}, "sub-1", SubmissionAction.APPROVE, supabase=fake
        )
    )
    assert result["submission_state"] == "APPROVED"
    # two transition rows appended (SUBMITTED->UNDER_REVIEW, UNDER_REVIEW->APPROVED)
    trans_inserts = [
        c
        for c in fake.calls
        if c.table_name == "marketplace_submission_transitions" and c.op == "insert"
    ]
    assert len(trans_inserts) == 2
    assert trans_inserts[0].payload["from_state"] == "SUBMITTED"
    assert trans_inserts[0].payload["to_state"] == "UNDER_REVIEW"
    assert trans_inserts[1].payload["to_state"] == "APPROVED"


def test_apply_admin_action_audit_failure_rolls_back_to_not_recorded(monkeypatch):
    fake = _admin_fake("UNDER_REVIEW")

    async def _boom(*a, **k):
        raise RuntimeError("audit sink down")

    monkeypatch.setattr(svc, "_record_admin_action", _boom)
    with pytest.raises(SubmissionServiceError) as ei:
        _run(
            apply_admin_action(
                {"id": "admin-1"},
                "sub-1",
                SubmissionAction.APPROVE,
                supabase=fake,
            )
        )
    assert ei.value.code == MARKETPLACE_ACTION_NOT_RECORDED
    # compensation reverted the state back to the prior UNDER_REVIEW
    revert = [
        c
        for c in fake.calls
        if c.table_name == "marketplace_submissions"
        and c.op == "update"
        and c.payload.get("submission_state") == "UNDER_REVIEW"
    ]
    assert revert, "expected a compensating update reverting to UNDER_REVIEW"


# ══════════════════════════════════════════════════════════════════════════
# admin_detail & immutability
# ══════════════════════════════════════════════════════════════════════════


def test_admin_detail_reads_evidence_copy_and_no_logic_columns():
    fake = FakeSupabase(
        script={
            ("marketplace_submissions", "select"): [
                {"id": "sub-1", "submission_state": "UNDER_REVIEW"}
            ],
            ("marketplace_backtest_evidence", "select"): [
                {"id": "ev-1", "condition_index": 0, "dataset": "BTCUSDT-0"}
            ],
            ("marketplace_submission_transitions", "select"): [
                {"from_state": "DRAFT", "to_state": "SUBMITTED"}
            ],
        }
    )
    detail = _run(admin_detail("sub-1", supabase=fake))
    assert detail["submission"]["id"] == "sub-1"
    assert detail["evidence"][0]["id"] == "ev-1"
    assert detail["transitions"][0]["to_state"] == "SUBMITTED"

    # never touched a source logic table
    touched = {c.table_name for c in fake.calls}
    assert "strategy_backtests" not in touched
    assert "strategy_versions" not in touched
    assert "strategies" not in touched

    # every select string must not name a blueprint / logic column
    for c in fake.calls:
        if c.op == "select":
            assert "blueprint" not in str(c.payload)
            assert "buy_logic" not in str(c.payload)
            assert "graph_json" not in str(c.payload)


def test_admin_detail_missing_submission_is_not_found():
    fake = FakeSupabase(script={("marketplace_submissions", "select"): []})
    with pytest.raises(SubmissionServiceError) as ei:
        _run(admin_detail("nope", supabase=fake))
    assert ei.value.code == MARKETPLACE_SUBMISSION_NOT_FOUND


def test_evidence_mutation_is_refused_immutable():
    err = refuse_evidence_mutation()
    assert err.code == MARKETPLACE_EVIDENCE_IMMUTABLE
    assert err.http_status == 409
