"""MKT-07 — the two mandatory Marketplace listing gates, and the ledger they read.

WHAT THE FINDING WAS
====================
The owner's remediation brief, Task 15 D, makes publication conditional on two things:

    1. "At least five valid, completed, persisted backtest results must exist for that
       strategy."
    2. "The strategy must have at least 24 hours of verifiable live algorithmic trading
       history. [...] Do not count paper trading as live trading for this requirement.
       Do not fabricate or manipulate live trading performance data."

Measured against the tree before this change:

    evidence_validator.MIN_CONDITIONS                       = 3     (brief says 5)
    any criterion reading live execution history            = none
    grep for a 24-hour / live-runtime publication threshold = no match

So requirement 1 was enforced at the wrong number and **requirement 2 was not enforced at
all**: a strategy that had never placed a live order could be listed, and the subscriber-facing
"verified live performance" claim had nothing behind it.

AND THE LEDGER WAS WRITABLE BY THE SELLER
=========================================
A gate is only as good as the evidence it reads. Measured on a cluster provisioned from
repository source, as role ``authenticated`` with ``request.jwt.claim.sub`` set to the
customer's own id (``.kiro-tmp/probe_exec_ledger.py``):

    INSERT a fabricated COMPLETED fill, exchange_id='binance',
           filled_at = now() - 48 hours                  *** SUCCEEDED ***
    UPDATE an existing own row's filled_size              *** SUCCEEDED ***
    DELETE an existing own row                            *** SUCCEEDED ***

``anon`` and ``authenticated`` both held SELECT/INSERT/UPDATE/DELETE on
``public.execution_records``, and the owner policy ``execution_records_user_access`` is
``FOR ALL`` with ``auth.uid() = user_id OR auth.uid() = tenant_id`` — so the customer
satisfies it for their own rows by construction. RLS was behaving exactly as written; the
privilege underneath it was wrong. The browser holds the anon key
(``algo22-terminal/src/supabase.js``), so PostgREST is reachable from any logged-in session.

Adding a live-history criterion on top of that would have been decoration: a seller could
have manufactured the 24 hours in one HTTP request. So the privilege is part of this change,
in ``migrations/hardening/999_privilege_hardening.sql`` SECTION 1b, and the postgres-lane
cases below are what hold it closed.

WHY THE REVOKE IS SAFE, MEASURED RATHER THAN ASSUMED
====================================================
Nothing in the repository writes ``execution_records``, ``orders`` or ``positions`` through
PostgREST — AST over every ``.table("<name>").insert/update/upsert/delete`` chain in
``backend_app`` plus grep across ``api_ws/``, ``scripts/``, ``dev_tools/`` and
``algo22-terminal/src`` (``.kiro-tmp/probe_table_writers.py``) finds zero writers. Every real
write goes through SQLAlchemy as the table owner, which is exempt from both the grant and the
policy. ``authenticated`` keeps SELECT.

WHAT IS NOT CLAIMED
===================
* **That ``strategy_backtests`` is equally protected. It is not.** It is the other half of the
  gate's evidence and ``authenticated`` holds INSERT/UPDATE/DELETE on it — but there the
  application's own writer uses the caller's client (``backend/backtest_service.py:336``), so
  revoking would stop every backtest persisting. Closing it means moving three writes to the
  service-role client first. ``test_the_other_half_of_the_evidence_is_still_writable`` pins
  that gap open so it cannot be forgotten, and fails when it is fixed.
* That a *testnet* fill is distinguished from a mainnet one. ``execution_records`` records no
  sandbox flag, so "live" here means "a venue order was placed", which is the same definition
  Trade History uses. The brief excludes paper trading, which this does exclude.
* Any statement about how many strategies currently qualify. No production figure was read for
  this file.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional
from unittest.mock import patch

import pytest

from backend_app.backend.marketplace import eligibility_gate as gate
from backend_app.backend.marketplace import evidence_validator as ev

OWNER_ID = "11111111-1111-4111-8111-111111111111"
STRATEGY_ID = "33333333-3333-4333-8333-333333333333"
VERSION_ID = "44444444-4444-4444-8444-444444444444"

#: The instant the synthetic fills are dated relative to. Fixed rather than ``now()`` so a
#: case cannot pass or fail on when the suite happens to run.
NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


# ══════════════════════════════════════════════════════════════════════════
# PART 1 — the thresholds themselves
# ══════════════════════════════════════════════════════════════════════════


def test_the_backtest_floor_is_five_as_the_brief_requires() -> None:
    """Task 15 D requirement 1, read off the constant the gate actually uses."""
    assert ev.MIN_CONDITIONS == 5
    assert ev.THRESHOLDS["MIN_CONDITIONS"] == 5
    assert ev.MIN_CONDITIONS <= ev.MAX_CONDITIONS


def test_the_api_field_bound_cannot_disagree_with_the_gate() -> None:
    """``SubmissionCreateRequest``'s bound is the gate's, not a second copy of the number.

    A literal here that drifted below the gate's floor would accept a request the gate then
    refuses with a criterion failure instead of a field error; one that drifted above would
    reject a legal request before the gate could admit it.
    """
    from backend_app.routers.library import SubmissionCreateRequest

    field = SubmissionCreateRequest.model_fields["backtest_ids"]
    bounds = {
        type(meta).__name__: meta
        for meta in field.metadata
    }
    rendered = repr(field.metadata)
    assert str(ev.MIN_CONDITIONS) in rendered, (
        f"the field bound {rendered} does not carry MIN_CONDITIONS "
        f"({ev.MIN_CONDITIONS}); it has drifted from the gate. {bounds}"
    )


def test_the_live_history_threshold_is_twenty_four_hours() -> None:
    """Task 15 D requirement 2, as a named constant rather than an inline literal."""
    assert gate.MIN_LIVE_HISTORY_HOURS == 24


def test_the_criterion_is_in_the_emitted_set_and_has_an_owner_facing_sentence() -> None:
    """A criterion the response cannot name is a refusal the owner cannot act on (Req 2.10)."""
    assert gate.MP_LIVE_HISTORY in gate.MP_CODES
    sentence = gate.MP_PUBLIC_MESSAGES[gate.MP_LIVE_HISTORY]
    assert sentence and sentence[0].isupper() and sentence.endswith(".")
    # The deny-list `tests/test_marketplace_error_surface.py` applies to every sentence in
    # this mapping; the one clause worth re-asserting here is that it tells the owner paper
    # trading will not do, because that is the half of the requirement a seller would
    # otherwise reasonably assume counts.
    assert "paper" in sentence.lower()


def test_the_evaluator_version_moved_with_the_criterion_set() -> None:
    """Requirement 2.11: a stored evaluation must name the rules it was decided under.

    An admission recorded under ``1.0.0`` was decided without ``MP_LIVE_HISTORY`` and against a
    floor of three backtests. Leaving the version string alone would make those two
    evaluations indistinguishable in the Audit_Log.
    """
    assert gate.EVALUATOR_VERSION == "eligibility-gate/1.1.0"


# ══════════════════════════════════════════════════════════════════════════
# PART 2 — what the criterion measures
# ══════════════════════════════════════════════════════════════════════════


def _fill(
    *,
    hours_ago: float,
    status: str = "COMPLETED",
    exchange_id: Optional[str] = "binance",
    filled_size: Any = "0.0100",
    stamp_field: str = "filled_at",
    execution_id: Optional[str] = None,
    is_sandbox: Optional[bool] = False,
) -> Dict[str, Any]:
    """One ``execution_records`` row as PostgREST would hand it back.

    ``filled_size`` is a string because the column is ``character varying`` in production. A
    builder that handed back a float would not exercise the parse the criterion depends on.
    """
    stamped = (NOW - timedelta(hours=hours_ago)).isoformat()
    row: Dict[str, Any] = {
        "execution_id": execution_id or f"exec-{uuid.uuid4().hex[:12]}",
        "strategy_id": STRATEGY_ID,
        "status": status,
        "exchange_id": exchange_id,
        # EXEC-ENV-01: a real-money fill unless a test says otherwise.
        "is_sandbox": is_sandbox,
        "filled_size": filled_size,
        "filled_at": None,
        "submitted_at": None,
        "created_at": None,
    }
    row[stamp_field] = stamped
    return row


def test_no_fills_is_not_twenty_four_hours() -> None:
    assert gate._live_history_hours([]) == 0.0


def test_one_fill_is_one_order_not_a_day_of_trading() -> None:
    """A single fill has no span. Zero rather than "the deployment has been up a while".

    This is the shape the brief's cross-cutting rule forbids in the other direction too: an
    unavailable span is not a zero-valued one, and the criterion refuses rather than guessing.
    """
    assert gate._live_history_hours([_fill(hours_ago=100)]) == 0.0


def test_two_fills_a_day_apart_measure_the_span() -> None:
    hours = gate._live_history_hours([_fill(hours_ago=30), _fill(hours_ago=4)])
    assert hours == pytest.approx(26.0)
    assert hours >= gate.MIN_LIVE_HISTORY_HOURS


def test_a_span_just_under_the_threshold_does_not_qualify() -> None:
    hours = gate._live_history_hours([_fill(hours_ago=23.9), _fill(hours_ago=0)])
    assert hours < gate.MIN_LIVE_HISTORY_HOURS


def test_a_fill_with_no_venue_is_not_live_history() -> None:
    """``exchange_id IS NULL`` is the clause that keeps non-venue rows out.

    This is also the structural reason paper trading cannot contribute: a paper fill never
    reaches ``execution_records`` at all (``test_paper01_paper_live_isolation`` pins that no
    module under ``backend/paper`` reaches a venue), and a row with no venue is refused here
    even if one somehow did.
    """
    rows = [_fill(hours_ago=48, exchange_id=None), _fill(hours_ago=0, exchange_id=None)]
    assert gate._live_history_hours(rows) == 0.0


def test_an_unfilled_or_pending_order_is_not_live_history() -> None:
    rows = [_fill(hours_ago=48, status="PENDING"), _fill(hours_ago=0, status="EXECUTING")]
    assert gate._live_history_hours(rows) == 0.0


def test_a_zero_or_unreadable_fill_quantity_is_not_a_fill() -> None:
    """Nothing traded, or the quantity cannot be read. Dropped, never read as zero-and-fine."""
    for bad in ("0", "0.0", "", None, "not-a-number"):
        rows = [
            _fill(hours_ago=48, filled_size=bad),
            _fill(hours_ago=0, filled_size=bad),
        ]
        assert gate._live_history_hours(rows) == 0.0, f"{bad!r} was counted as a fill"


def test_the_span_falls_back_through_the_instant_columns_in_order() -> None:
    """``filled_at``, else ``submitted_at``, else ``created_at`` — the order orders.py uses."""
    rows = [
        _fill(hours_ago=48, stamp_field="submitted_at"),
        _fill(hours_ago=0, stamp_field="created_at"),
    ]
    assert gate._live_history_hours(rows) == pytest.approx(48.0)


def test_a_row_with_no_readable_instant_is_dropped() -> None:
    undated = _fill(hours_ago=0)
    undated["filled_at"] = None
    rows = [_fill(hours_ago=48), undated]
    assert gate._live_history_hours(rows) == 0.0


def test_the_same_fill_read_twice_is_still_one_fill() -> None:
    """The read asks for both ends of the history, so a short history returns rows twice.

    Distinctness is on ``execution_id``, so a duplicated row cannot become a second fill and
    manufacture a span against itself.
    """
    one = _fill(hours_ago=48, execution_id="exec-same")
    assert gate._live_history_hours([one, dict(one)]) == 0.0


def test_a_naive_timestamp_is_read_as_utc_rather_than_raising() -> None:
    """Every column in the projection is ``timestamp with time zone``.

    A naive value therefore means the driver dropped the offset. Reading it as UTC keeps the
    subtraction well-defined; raising inside a criterion would surface as ``unevaluable`` and
    turn a driver quirk into a 503.
    """
    rows = [
        {
            "execution_id": "exec-naive-1",
            "status": "COMPLETED",
            "exchange_id": "binance",
            "filled_size": "1",
            "filled_at": "2026-05-30T12:00:00",
        },
        {
            "execution_id": "exec-naive-2",
            "status": "COMPLETED",
            "exchange_id": "binance",
            "filled_size": "1",
            "filled_at": datetime(2026, 6, 1, 12, 0, 0),
        },
    ]
    assert gate._live_history_hours(rows) == pytest.approx(48.0)


# ══════════════════════════════════════════════════════════════════════════
# PART 3 — the criterion inside a whole evaluation
# ══════════════════════════════════════════════════════════════════════════


class _Table:
    """A chain-able table double that applies the filters the gate's five reads send.

    Filters are applied rather than ignored. A permissive double would return the seeded
    execution rows for any ``strategy_id``, so a gate that forgot its owner scope would still
    pass — which is the class of vacuity the schema-faithful double in
    ``test_marketplace_eligibility_tenant_verdict.py`` exists to prevent.
    """

    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self._rows = rows
        self._filters: List[Any] = []

    def select(self, *_a: Any, **_k: Any) -> "_Table":
        return self

    def eq(self, column: str, value: Any) -> "_Table":
        self._filters.append(("eq", column, value))
        return self

    def in_(self, column: str, values: Any) -> "_Table":
        self._filters.append(("in", column, values))
        return self

    @property
    def not_(self) -> "_Table":
        self._filters.append(("negate-next", None, None))
        return self

    def is_(self, column: str, value: Any) -> "_Table":
        negated = self._filters and self._filters[-1][0] == "negate-next"
        if negated:
            self._filters.pop()
        self._filters.append(("not_is" if negated else "is", column, value))
        return self

    def order(self, *_a: Any, **_k: Any) -> "_Table":
        return self

    def limit(self, *_a: Any, **_k: Any) -> "_Table":
        return self

    def execute(self) -> Any:
        rows = list(self._rows)
        for kind, column, value in self._filters:
            if kind == "eq":
                rows = [r for r in rows if str(r.get(column, "")) == str(value)]
            elif kind == "in":
                wanted = {str(v) for v in value}
                rows = [r for r in rows if str(r.get(column, "")) in wanted]
            elif kind == "is" and str(value).lower() == "null":
                rows = [r for r in rows if r.get(column) is None]
            elif kind == "not_is" and str(value).lower() == "null":
                rows = [r for r in rows if r.get(column) is not None]
        return type("_Resp", (), {"data": rows, "error": None})()


class _FailingTable(_Table):
    """A table whose read answers an error, as PostgREST does on a bad column or a timeout."""

    def execute(self) -> Any:
        return type("_Resp", (), {"data": None, "error": "57014 statement timeout"})()


class _DB:
    def __init__(self, failing: Optional[str] = None) -> None:
        self.stores: Dict[str, List[Dict[str, Any]]] = {}
        self._failing = failing

    def table(self, name: str) -> _Table:
        rows = self.stores.setdefault(name, [])
        if name == self._failing:
            return _FailingTable(rows)
        return _Table(rows)


class _NoopAudit:
    async def record_or_raise(self, *_a: Any, **_k: Any) -> None:
        return None

    async def log(self, *_a: Any, **_k: Any) -> None:
        return None


_WINDOWS = (
    ("2024-01-01", "2024-06-30", "BTCUSD-2024H1", "chk-btc-h1"),
    ("2024-07-01", "2024-12-31", "ETHUSD-2024H2", "chk-eth-h2"),
    ("2023-01-01", "2023-06-30", "SOLUSD-2023H1", "chk-sol-h1"),
    ("2023-07-01", "2023-12-31", "ADAUSD-2023H2", "chk-ada-h2"),
    ("2022-01-01", "2022-06-30", "XRPUSD-2022H1", "chk-xrp-h1"),
    ("2022-07-01", "2022-12-31", "DOTUSD-2022H2", "chk-dot-h2"),
    ("2021-01-01", "2021-06-30", "LTCUSD-2021H1", "chk-ltc-h1"),
    ("2021-07-01", "2021-12-31", "LNKUSD-2021H2", "chk-lnk-h2"),
    ("2020-01-01", "2020-06-30", "BCHUSD-2020H1", "chk-bch-h1"),
    ("2020-07-01", "2020-12-31", "ATOMUS-2020H2", "chk-atm-h2"),
)


def _seed(*, backtests: int, live_hours: Optional[float], is_sandbox: Optional[bool] = False) -> "_DB":
    """A strategy with ``backtests`` eligible runs and, optionally, ``live_hours`` of history."""
    db = _DB()
    db.stores["strategies"] = [
        {"id": STRATEGY_ID, "user_id": OWNER_ID, "archived_at": None}
    ]
    db.stores["strategy_versions"] = [
        {
            "id": VERSION_ID,
            "strategy_id": STRATEGY_ID,
            "version": 1,
            "is_draft": False,
            "validation_state": "VALID",
            "blueprint": {"nodes": [{"type": "action"}]},
            "graph_json": {"nodes": [{"type": "action"}]},
        }
    ]
    db.stores["strategy_backtests"] = [
        {
            "id": str(uuid.uuid4()),
            "user_id": OWNER_ID,
            "strategy_id": STRATEGY_ID,
            "version_id": VERSION_ID,
            "status": "completed",
            "completed_at": "2026-06-01T00:00:00+00:00",
            "error_message": None,
            "dataset": dataset,
            "start_date": start,
            "end_date": end,
            "initial_capital": 10000,
            "commission": 0.001,
            "slippage": 0.0005,
            "dataset_checksum": checksum,
            "dag_hash": f"dag-{checksum}",
            "total_trades": 120,
            "executed_bar_count": 500,
            "total_return_pct": 42.5,
            "sharpe_ratio": 1.8,
            "max_drawdown": -5.2,
            "win_rate": 61.0,
            "profit_factor": 1.7,
            "final_capital": 14250.0,
        }
        for start, end, dataset, checksum in _WINDOWS[:backtests]
    ]
    db.stores["marketplace_submissions"] = []
    db.stores["library_strategies"] = []
    db.stores["execution_records"] = []
    if live_hours is not None:
        for hours_ago in (live_hours, 0.0):
            row = _fill(hours_ago=hours_ago, is_sandbox=is_sandbox)
            row["tenant_id"] = OWNER_ID
            db.stores["execution_records"].append(row)
    return db


def _evaluate(db: "_DB") -> Any:
    backtest_ids = [r["id"] for r in db.stores["strategy_backtests"]]
    caller = {"id": OWNER_ID, "tenant_id": OWNER_ID}
    with patch(
        "backend_app.core.audit_trail.get_strategy_audit_logger",
        return_value=_NoopAudit(),
    ):
        return asyncio.run(gate.evaluate(caller, STRATEGY_ID, backtest_ids, db))


def _failed(verdict: Any) -> set:
    return {o.code for o in verdict.failed_outcomes}


def test_a_strategy_that_never_traded_live_is_refused_on_the_live_criterion() -> None:
    """The headline case: everything else passes and publication is still refused."""
    verdict = _evaluate(_seed(backtests=ev.MIN_CONDITIONS, live_hours=None))
    assert verdict.unevaluable is False
    assert verdict.admitted is False
    assert gate.MP_LIVE_HISTORY in _failed(verdict)
    # Nothing else is at fault, which is what makes this a test of the new criterion rather
    # than of a badly seeded strategy.
    assert _failed(verdict) == {gate.MP_LIVE_HISTORY}


def test_a_strategy_with_a_full_day_of_live_history_is_admitted() -> None:
    verdict = _evaluate(
        _seed(backtests=ev.MIN_CONDITIONS, live_hours=gate.MIN_LIVE_HISTORY_HOURS + 1)
    )
    assert verdict.unevaluable is False, _failed(verdict)
    assert verdict.admitted is True, _failed(verdict)
    assert verdict.version_id == VERSION_ID


def test_exec_env01_a_day_of_testnet_fills_is_not_live_history() -> None:
    """EXEC-ENV-01: Binance testnet fills carry an exchange_id too. They are never live
    performance -- strategy FROM had 15.7 h of them in production toward this bar."""
    verdict = _evaluate(
        _seed(backtests=ev.MIN_CONDITIONS, live_hours=gate.MIN_LIVE_HISTORY_HOURS + 1,
              is_sandbox=True)
    )
    assert verdict.admitted is False
    assert _failed(verdict) == {gate.MP_LIVE_HISTORY}


def test_exec_env01_fills_whose_environment_was_never_recorded_do_not_count() -> None:
    verdict = _evaluate(
        _seed(backtests=ev.MIN_CONDITIONS, live_hours=gate.MIN_LIVE_HISTORY_HOURS + 1,
              is_sandbox=None)
    )
    assert verdict.admitted is False
    assert _failed(verdict) == {gate.MP_LIVE_HISTORY}


def test_a_strategy_whose_live_history_is_short_of_a_day_is_refused() -> None:
    verdict = _evaluate(
        _seed(backtests=ev.MIN_CONDITIONS, live_hours=gate.MIN_LIVE_HISTORY_HOURS - 1)
    )
    assert verdict.admitted is False
    assert _failed(verdict) == {gate.MP_LIVE_HISTORY}


def test_four_backtests_is_refused_where_five_is_admitted() -> None:
    """The floor is doing work, measured as the difference one run makes."""
    short = _evaluate(
        _seed(backtests=ev.MIN_CONDITIONS - 1, live_hours=gate.MIN_LIVE_HISTORY_HOURS + 1)
    )
    assert short.admitted is False
    assert ev.EV_COUNT in _failed(short)

    enough = _evaluate(
        _seed(backtests=ev.MIN_CONDITIONS, live_hours=gate.MIN_LIVE_HISTORY_HOURS + 1)
    )
    assert enough.admitted is True, _failed(enough)


def test_another_tenants_live_history_does_not_qualify_this_strategy() -> None:
    """The fifth read is owner-scoped, so a foreign tenant's fills are simply not returned."""
    db = _seed(backtests=ev.MIN_CONDITIONS, live_hours=gate.MIN_LIVE_HISTORY_HOURS + 1)
    for row in db.stores["execution_records"]:
        row["tenant_id"] = "99999999-9999-4999-8999-999999999999"
    verdict = _evaluate(db)
    assert verdict.admitted is False
    assert gate.MP_LIVE_HISTORY in _failed(verdict)


def test_another_strategys_live_history_does_not_qualify_this_one() -> None:
    db = _seed(backtests=ev.MIN_CONDITIONS, live_hours=gate.MIN_LIVE_HISTORY_HOURS + 1)
    for row in db.stores["execution_records"]:
        row["strategy_id"] = str(uuid.uuid4())
    verdict = _evaluate(db)
    assert verdict.admitted is False
    assert gate.MP_LIVE_HISTORY in _failed(verdict)


def test_a_failed_live_history_read_is_unevaluable_not_an_admission() -> None:
    """Requirement 2.13, extended to the fifth read.

    A read that did not complete means eligibility is *unknown*. The gate must answer 503
    rather than either admitting (the dangerous direction — publication on unread evidence) or
    refusing with a criterion the owner cannot act on.
    """
    db = _seed(backtests=ev.MIN_CONDITIONS, live_hours=gate.MIN_LIVE_HISTORY_HOURS + 1)
    db._failing = "execution_records"
    verdict = _evaluate(db)
    assert verdict.unevaluable is True
    assert verdict.admitted is False
    assert verdict.outcomes == ()


def test_the_fifth_read_asks_for_its_own_filters_and_both_ends() -> None:
    """The read, not just the verdict: owner scope, venue clause, ordering and bound.

    Observed through a recording double rather than by reading the source, because a filter
    that is spelled but not sent is exactly the failure this would otherwise miss.
    """
    calls: List[Dict[str, Any]] = []

    class _Recording(_Table):
        def __init__(self, rows: List[Dict[str, Any]]) -> None:
            super().__init__(rows)
            self._record: Dict[str, Any] = {"eq": {}, "not_is": [], "order": [], "limit": []}
            calls.append(self._record)

        def eq(self, column: str, value: Any) -> "_Recording":
            self._record["eq"][column] = value
            return super().eq(column, value)  # type: ignore[return-value]

        def is_(self, column: str, value: Any) -> "_Recording":
            if self._filters and self._filters[-1][0] == "negate-next":
                self._record["not_is"].append((column, value))
            return super().is_(column, value)  # type: ignore[return-value]

        def order(self, *a: Any, **k: Any) -> "_Recording":
            self._record["order"].append((a, k))
            return self

        def limit(self, *a: Any, **k: Any) -> "_Recording":
            self._record["limit"].append(a)
            return self

    db = _seed(backtests=ev.MIN_CONDITIONS, live_hours=gate.MIN_LIVE_HISTORY_HOURS + 1)

    def _table(name: str) -> Any:
        rows = db.stores.setdefault(name, [])
        return _Recording(rows) if name == "execution_records" else _Table(rows)

    db.table = _table  # type: ignore[assignment]
    verdict = _evaluate(db)
    assert verdict.admitted is True, _failed(verdict)

    assert len(calls) == 2, "the live history is read from both ends, so twice"
    for record in calls:
        assert record["eq"]["strategy_id"] == STRATEGY_ID
        assert record["eq"]["tenant_id"] == OWNER_ID
        assert record["eq"]["status"] == "COMPLETED"
        assert ("exchange_id", "null") in record["not_is"]
        assert record["limit"] == [(gate.LIVE_HISTORY_END_WINDOW,)]
    directions = [k.get("desc") for record in calls for _a, k in record["order"]]
    assert sorted(directions, key=bool) == [False, True], (
        "one ascending and one descending read, so the earliest and latest fills are both "
        f"reached; got {directions}"
    )

    # The SORT KEY must be the NOT NULL column, not the column the span is dated from.
    # Ordering by nullable `filled_at` puts the nulls at one end (NULLS LAST ascending,
    # NULLS FIRST descending in PostgreSQL), so a strategy with more fills than the window
    # whose `filled_at` was never stamped would have its descending slice filled entirely
    # with those rows and its genuinely latest fill excluded -- a false FAIL, and a
    # user-visible one.
    sort_keys = {a[0] for record in calls for a, _k in record["order"]}
    assert sort_keys == {gate._LIVE_EXECUTION_ORDER_COLUMN}, (
        f"the end-windows sort by {sort_keys}; expected only "
        f"{gate._LIVE_EXECUTION_ORDER_COLUMN!r}, which is the NOT NULL column"
    )
    assert gate._LIVE_EXECUTION_ORDER_COLUMN == "created_at"


def test_the_fifth_read_names_only_declared_columns() -> None:
    """A projection naming an undeclared column answers 42703 and makes every evaluation a 503.

    That is not hypothetical here: it is exactly what ``strategies.tenant_id`` did to this gate
    in production. The manifest is the oracle, and it carries the production measurement.
    """
    from backend_app.backend.marketplace import _ELIGIBILITY_HANDLER

    declared = _ELIGIBILITY_HANDLER["tables"]["execution_records"]
    projected = {
        part.strip()
        for part in gate._LIVE_EXECUTION_PROJECTION.split(",")
        if part.strip()
    }
    assert projected <= declared, f"undeclared: {sorted(projected - declared)}"
    # The filters are columns too, and a filter on an absent column fails the same way.
    assert {"strategy_id", "tenant_id", "status", "exchange_id"} <= declared


# ══════════════════════════════════════════════════════════════════════════
# PART 4 — the ledger the criterion reads, against real PostgreSQL
# ══════════════════════════════════════════════════════════════════════════
#
# Same gate as tests/test_bill07_billing_invoices_postgres.py and
# tests/test_sup02_support_columns_postgres.py, and deliberately with no fallback to a
# local DSN. A SKIP here is NOT a pass -- it means the forgery path is unverified.

_PG_URL = os.environ.get("AERORA_TEST_DATABASE_URL", "")
_HAS_PG = _PG_URL.startswith("postgres")
pg_only = pytest.mark.skipif(
    not _HAS_PG,
    reason=(
        "requires PostgreSQL: set AERORA_TEST_DATABASE_URL to a postgres DSN. "
        "A SKIP here is NOT a pass -- the live-execution ledger is unverified."
    ),
)

#: The three ledgers SECTION 1b covers. Each one has zero PostgREST writers in the tree, so
#: none of the browser roles needs a write privilege on any of them.
_LEDGERS = ("execution_records", "orders", "positions")


@pytest.fixture()
def pg():
    import psycopg2

    conn = psycopg2.connect(_PG_URL)
    conn.autocommit = False
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


def _grants(cur, table: str, role: str) -> set:
    cur.execute(
        "SELECT privilege_type FROM information_schema.role_table_grants "
        " WHERE table_schema='public' AND table_name=%s AND grantee=%s",
        (table, role),
    )
    return {r[0] for r in cur.fetchall()}


@pg_only
@pytest.mark.parametrize("table", _LEDGERS)
def test_no_browser_role_can_write_an_execution_ledger(pg, table: str) -> None:
    """The privilege, read off the catalogue rather than inferred from the migration text."""
    cur = pg.cursor()
    cur.execute("SELECT to_regclass(%s)", (f"public.{table}",))
    if cur.fetchone()[0] is None:
        pytest.skip(f"public.{table} is not in this database")

    assert _grants(cur, table, "anon") == set(), (
        f"anon still holds {sorted(_grants(cur, table, 'anon'))} on {table}"
    )
    authenticated = _grants(cur, table, "authenticated")
    assert authenticated <= {"SELECT"}, (
        f"authenticated holds {sorted(authenticated - {'SELECT'})} on {table} beyond "
        "SELECT; a customer must not be able to write the ledger that judges them"
    )
    # Not an afterthought: the gate and Trade History both READ this table through the
    # owner, but leaving SELECT means a future owner-scoped read needs no privilege change,
    # and a read is not a forgery.
    assert "SELECT" in authenticated


@pg_only
def test_a_customer_cannot_forge_its_own_live_execution_history(pg) -> None:
    """The measurement that justified the change, as a regression.

    Adopts the customer's identity the way a policy sees it -- ``request.jwt.claim.sub`` plus
    ``SET LOCAL ROLE authenticated`` -- and attempts the exact insert a seller would need to
    manufacture a qualifying 24-hour history.
    """
    import psycopg2

    cur = pg.cursor()
    cur.execute("SELECT to_regclass('public.execution_records')")
    if cur.fetchone()[0] is None:
        pytest.skip("public.execution_records is not in this database")

    user_id = str(uuid.uuid4())
    cur.execute(
        "INSERT INTO auth.users (id, email) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
        (user_id, f"mkt07-{user_id[:8]}@example.invalid"),
    )
    cur.execute(
        "INSERT INTO profiles (id, email) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
        (user_id, f"mkt07-{user_id[:8]}@example.invalid"),
    )

    cur.execute("SAVEPOINT forge")
    cur.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (user_id,))
    cur.execute("SELECT set_config('request.jwt.claim.role', 'authenticated', true)")
    cur.execute("SET LOCAL ROLE authenticated")
    cur.execute("SELECT auth.uid()")
    assert str(cur.fetchone()[0]) == user_id, (
        "the probe did not adopt the customer's identity, so a refusal below would prove "
        "nothing about the privilege"
    )

    with pytest.raises((psycopg2.errors.InsufficientPrivilege, psycopg2.ProgrammingError)):
        cur.execute(
            """
            INSERT INTO execution_records
                (execution_id, tenant_id, user_id, strategy_id, symbol, side, size,
                 filled_size, price, status, exchange_id, submitted_at, filled_at,
                 created_at, updated_at)
            VALUES (%s, %s, %s, 'forged', 'BTC/USDT', 'BUY', '1', '1', '50000',
                    'COMPLETED', 'binance', now() - interval '48 hours',
                    now() - interval '48 hours', now(), now())
            """,
            (f"forged-{uuid.uuid4().hex[:10]}", user_id, user_id),
        )
    pg.rollback()


@pg_only
def test_a_seller_cannot_write_the_backtest_half_of_its_own_evidence(pg) -> None:
    """MKT-10, and the inverse of the pinned gap this file used to carry.

    ``strategy_backtests`` is the five-backtest half of the same gate. Until the writer
    moved, ``authenticated`` held INSERT/UPDATE/DELETE here and this test asserted the
    OPPOSITE -- it was a deliberate failing-when-fixed pin, because revoking before moving
    the application's own writer would have stopped every backtest from persisting.

    What changed: ``backend/backtest_service.py`` performs its three writes through the
    service-role client, so ``074_mkt10_revoke_backtest_dml.sql`` can take the privilege
    away. This was the LIVE half of the pair -- measured against production, where the
    three ledgers above had already been narrowed to SELECT but this table had not.

    SELECT is asserted to REMAIN: the reads were deliberately left on the caller's own
    client, so revoking it would break Backtest History for every user.
    """
    cur = pg.cursor()
    cur.execute("SELECT to_regclass('public.strategy_backtests')")
    if cur.fetchone()[0] is None:
        pytest.skip("public.strategy_backtests is not in this database")

    assert _grants(cur, "strategy_backtests", "anon") == set(), (
        "anon still holds "
        f"{sorted(_grants(cur, 'strategy_backtests', 'anon'))} on strategy_backtests; "
        "that key ships to unauthenticated visitors in the browser"
    )

    authenticated = _grants(cur, "strategy_backtests", "authenticated")
    assert authenticated <= {"SELECT"}, (
        f"authenticated holds {sorted(authenticated - {'SELECT'})} on strategy_backtests "
        "beyond SELECT, so a seller can still author the backtest evidence the "
        "Eligibility_Gate reads about them"
    )
    assert "SELECT" in authenticated, (
        "authenticated lost SELECT on strategy_backtests -- the backtest READS still run "
        "as the caller (get_backtest, list_backtests, get_backtest_history, "
        "compare_backtests), so Backtest History is now broken for every user"
    )


@pg_only
def test_a_customer_cannot_forge_a_backtest_row(pg) -> None:
    """The privilege above, demonstrated as a refusal rather than read off the catalogue.

    Mirrors ``test_a_customer_cannot_forge_its_own_live_execution_history``: adopt the
    customer's identity, attempt the forgery the Eligibility_Gate would have counted, and
    require the database to refuse it. A seller writing five passing backtests directly
    against PostgREST is the exact move this closes.
    """
    import psycopg2

    cur = pg.cursor()
    cur.execute("SELECT to_regclass('public.strategy_backtests')")
    if cur.fetchone()[0] is None:
        pytest.skip("public.strategy_backtests is not in this database")

    user_id = str(uuid.uuid4())
    cur.execute(
        "INSERT INTO auth.users (id, email) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
        (user_id, f"mkt10-{user_id[:8]}@example.invalid"),
    )
    cur.execute(
        "INSERT INTO profiles (id, email) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
        (user_id, f"mkt10-{user_id[:8]}@example.invalid"),
    )

    cur.execute("SAVEPOINT forge_backtest")
    cur.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (user_id,))
    cur.execute("SELECT set_config('request.jwt.claim.role', 'authenticated', true)")
    cur.execute("SET LOCAL ROLE authenticated")
    cur.execute("SELECT auth.uid()")
    assert str(cur.fetchone()[0]) == user_id, (
        "the probe did not adopt the customer's identity, so a refusal below would prove "
        "nothing about the privilege"
    )

    # `user_id = auth.uid()` holds, so `backtests_owner_access` is SATISFIED. Only the
    # missing INSERT privilege can refuse this -- which is the point: RLS was never the
    # thing that was wrong here.
    with pytest.raises((psycopg2.errors.InsufficientPrivilege, psycopg2.ProgrammingError)):
        cur.execute(
            """
            INSERT INTO strategy_backtests
                (id, user_id, strategy_id, status, created_at)
            VALUES (%s, %s, %s, 'completed', now())
            """,
            (str(uuid.uuid4()), user_id, str(uuid.uuid4())),
        )
    pg.rollback()
