"""
tests/test_expiry_sweep.py - the Subscription expiry sweep's unit tests.

Spec: marketplace-subscriptions-paper-trading task 20.1. Requirements 11.7, 11.8, 11.10, 11.12,
11.15, 24.4, 26.2, 26.5.

WHAT THIS FILE PINS
-------------------
1. Only the eligible rows transition - ``period_expiry IS NOT NULL AND period_expiry <= now AND
   status IN ('active','suspended')``, and nothing else.
2. An already-``expired`` row is untouched. This is the clause that makes the sweep idempotent
   (property P-13, asserted as a property by task 20.2), so it is pinned as a named example
   here too.
3. A row with a null ``period_expiry`` is untouched - it has no period, so it has not expired.
4. A ``cancelled`` and a ``refunded`` row are untouched: Requirement 11.2 gives neither an edge
   to ``EXPIRED``, and a cancelled purchaser keeps entitlement to the unchanged expiry
   (Requirement 11.9).
5. Exactly one ``library_subscription_transitions`` row and exactly one audit entry per expired
   Subscription (Requirements 11.12, 26.2).
6. Running the sweep twice changes nothing the second time.
7. A failure is **surfaced**, not swallowed - at the candidate read, at the update, at the
   history insert and at the audit write - and ``last_run_at`` does not advance for a failed
   pass.
8. The sweep cannot produce an ``active`` row, by any of the four independent mechanisms the
   module uses to make that true.

WHY ``_run_coroutine`` AND NOT ``asyncio.run``
----------------------------------------------
Lifted from ``tests/test_marketplace_checkout_regression.py`` for the reason recorded there:
``asyncio.run`` closes its loop and leaves the main thread with *no* current loop, and other
suites in this repository still call the deprecated
``asyncio.get_event_loop().run_until_complete(...)`` - which then raises ``RuntimeError: There is
no current event loop in thread 'MainThread'``. ``tests/test_marketplace_pipeline.py`` is one of
them, so running these two files in the same session with a bare ``asyncio.run`` here would
break a module collected after this one. A test file that breaks its neighbours is a new
regression, not a guard against one.
"""

from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import pytest

from backend_app.backend.marketplace import COLUMN_CONTRACT
from backend_app.backend.marketplace import expiry_sweep as sweep_module
from backend_app.backend.marketplace.expiry_sweep import (
    EXPIRABLE_STATUS_TEXTS,
    EXPIRED_STATUS_TEXT,
    SWEEP_INTERVAL_SECONDS,
    SWEEP_TARGET_STATE,
    ExpiredSubscription,
    ExpirySweepFailed,
    SweepOutcome,
    sweep,
)
from backend_app.backend.marketplace.subscription_state import (
    STATUS_TEXT_FOR_STATE,
    SubscriptionState,
    can_transition,
)

NOW = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
PURCHASER = "purchaser-1"
LISTING = "listing-1"


# ══════════════════════════════════════════════════════════════════════════
# The loop runner (see the module docstring)
# ══════════════════════════════════════════════════════════════════════════


def _run_coroutine(coro: Any) -> Any:
    """Run ``coro`` to completion **without leaving the thread without an event loop**."""
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


# ══════════════════════════════════════════════════════════════════════════
# A Supabase double that actually applies the sweep's filters
# ══════════════════════════════════════════════════════════════════════════
#
# The filters are evaluated rather than merely recorded, because the assertions that matter here
# are about WHICH rows move. A double that accepted ``.lte`` and ``.in_`` and then updated
# everything would make every one of these tests pass against a sweep with no predicate at all.


class _Resp:
    def __init__(self, data: Any) -> None:
        self.data = data


class _Not:
    """The ``.not_`` accessor of the PostgREST chain. Only ``is_`` is needed."""

    def __init__(self, query: "_Query") -> None:
        self._query = query

    def is_(self, column: str, value: Any) -> "_Query":
        self._query.filters.append(("not.is", column, value))
        return self._query


class _Query:
    """A recording, *filter-applying* query builder mimicking the supabase-py fluent chain."""

    def __init__(self, table: str, client: "FakeSupabase") -> None:
        self.table_name = table
        self.client = client
        self.op: str = "select"
        self.payload: Optional[Dict[str, Any]] = None
        self.cols: Optional[str] = None
        self.filters: List[Tuple[str, str, Any]] = []

    # -- verbs -------------------------------------------------------------
    def select(self, cols: str) -> "_Query":
        self.op = "select"
        self.cols = cols
        return self

    def update(self, payload: Dict[str, Any]) -> "_Query":
        self.op = "update"
        self.payload = copy.deepcopy(payload)
        return self

    def insert(self, payload: Dict[str, Any]) -> "_Query":
        self.op = "insert"
        self.payload = copy.deepcopy(payload)
        return self

    def delete(self) -> "_Query":
        self.op = "delete"
        return self

    # -- filters -----------------------------------------------------------
    @property
    def not_(self) -> _Not:
        return _Not(self)

    def eq(self, column: str, value: Any) -> "_Query":
        self.filters.append(("eq", column, value))
        return self

    def lte(self, column: str, value: Any) -> "_Query":
        self.filters.append(("lte", column, value))
        return self

    def in_(self, column: str, values: Any) -> "_Query":
        self.filters.append(("in", column, list(values)))
        return self

    def execute(self) -> Any:
        return self.client._execute(self)


def _as_instant(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class FakeSupabase:
    """In-memory tables that honour ``eq``, ``lte``, ``in`` and ``not.is`` NULL.

    Records every executed statement on :attr:`statements` so a test can count round trips and
    inspect the payloads, and can be told to fail a specific ``(op, table)`` pair either by
    raising or by returning a PostgREST error envelope - the two shapes a real failure takes.
    """

    def __init__(
        self,
        *,
        subscriptions: Optional[List[Dict[str, Any]]] = None,
        transitions: Optional[List[Dict[str, Any]]] = None,
        deployments: Optional[List[Dict[str, Any]]] = None,
        paper_sessions: Optional[List[Dict[str, Any]]] = None,
        raise_on: Optional[set] = None,
        error_on: Optional[set] = None,
    ) -> None:
        self.tables: Dict[str, List[Dict[str, Any]]] = {
            "library_subscriptions": [dict(r) for r in (subscriptions or [])],
            "library_subscription_transitions": [dict(r) for r in (transitions or [])],
            "strategy_deployments": [dict(r) for r in (deployments or [])],
            "paper_sessions": [dict(r) for r in (paper_sessions or [])],
        }
        self.raise_on = raise_on or set()
        self.error_on = error_on or set()
        self.statements: List[_Query] = []

    # -- the supabase-py surface the sweep uses ----------------------------
    def table(self, name: str) -> _Query:
        return _Query(name, self)

    # -- helpers for the assertions ----------------------------------------
    def ops(self) -> List[Tuple[str, str]]:
        return [(q.op, q.table_name) for q in self.statements]

    def rows(self, table: str) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.tables.get(table, [])]

    def statuses(self) -> Dict[str, Any]:
        return {r["id"]: r.get("status") for r in self.tables["library_subscriptions"]}

    def snapshot(self) -> Dict[str, List[Dict[str, Any]]]:
        return {name: [dict(r) for r in rows] for name, rows in self.tables.items()}

    # -- execution ---------------------------------------------------------
    def _execute(self, q: _Query) -> Any:
        self.statements.append(q)
        key = (q.op, q.table_name)
        if key in self.raise_on:
            raise RuntimeError(f"{q.op} failed on {q.table_name}")
        if key in self.error_on:
            return {"data": None, "error": {"message": f"boom on {q.table_name}"}}

        rows = self.tables.setdefault(q.table_name, [])
        if q.op == "select":
            return _Resp([dict(r) for r in self._matching(rows, q)])
        if q.op == "insert":
            row = dict(q.payload or {})
            row.setdefault("id", f"{q.table_name}-{len(rows) + 1}")
            rows.append(row)
            return _Resp([dict(row)])
        if q.op == "update":
            touched = self._matching(rows, q)
            for row in touched:
                row.update(q.payload or {})
            return _Resp([dict(r) for r in touched])
        return _Resp([])

    def _matching(self, rows: List[Dict[str, Any]], q: _Query) -> List[Dict[str, Any]]:
        matched = list(rows)
        for kind, column, value in q.filters:
            if kind == "eq":
                matched = [r for r in matched if str(r.get(column)) == str(value)]
            elif kind == "in":
                wanted = {str(v) for v in value}
                matched = [r for r in matched if str(r.get(column)) in wanted]
            elif kind == "lte":
                bound = _as_instant(value)
                kept = []
                for r in matched:
                    left = _as_instant(r.get(column))
                    # SQL three-valued logic: NULL <= x is unknown, so the row is not matched.
                    if left is not None and bound is not None and left <= bound:
                        kept.append(r)
                matched = kept
            elif kind == "not.is":
                matched = [r for r in matched if r.get(column) is not None]
        return matched


class _RecordingAuditWriter:
    """Records the audit calls the sweep makes, and can be told to fail."""

    def __init__(self, *, fail_for: Optional[set] = None) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.fail_for = fail_for or set()

    async def __call__(self, action: Any, **kwargs: Any) -> Any:
        if kwargs.get("resource_id") in self.fail_for:
            raise RuntimeError("audit backend unavailable")
        self.calls.append({"action": action, **kwargs})
        return {"audit_id": f"AUDIT-{len(self.calls)}"}


# ══════════════════════════════════════════════════════════════════════════
# Row builders
# ══════════════════════════════════════════════════════════════════════════


def _subscription(
    sub_id: str,
    status: str,
    *,
    period_expiry: Optional[datetime],
    user_id: str = PURCHASER,
    library_id: str = LISTING,
) -> Dict[str, Any]:
    return {
        "id": sub_id,
        "user_id": user_id,
        "library_id": library_id,
        "status": status,
        "period_expiry": period_expiry.isoformat() if period_expiry else None,
        "updated_at": (NOW - timedelta(days=30)).isoformat(),
    }


PAST = NOW - timedelta(minutes=5)
FUTURE = NOW + timedelta(days=10)


def _mixed_population(anchor: datetime = NOW) -> List[Dict[str, Any]]:
    """One row per interesting case, so a single sweep exercises the whole predicate.

    ``anchor`` is the instant the population is built *relative to*, and it must be the same
    instant the sweep under test will compare against. It defaults to :data:`NOW` because almost
    every test here injects ``now=NOW`` and wants the boundary row to sit exactly on it.

    The one caller that must override it is the worker test: ``MarketplaceExpiryWorker`` passes
    no ``now``, so its sweep reads the **wall clock**, and a population anchored to a fixed
    ``NOW`` in the past would hand it a ``sub-active-future`` row whose "future" expiry is years
    behind the real present. That row would then expire - correctly, by the predicate - and the
    test would be measuring the mismatch between two clocks rather than the worker's contract.
    """
    past = anchor - timedelta(minutes=5)
    future = anchor + timedelta(days=10)
    return [
        # -- eligible ------------------------------------------------------
        _subscription("sub-active-past", "active", period_expiry=past),
        _subscription("sub-suspended-past", "suspended", period_expiry=past),
        _subscription("sub-active-at-boundary", "active", period_expiry=anchor),
        # -- ineligible ----------------------------------------------------
        _subscription("sub-active-future", "active", period_expiry=future),
        _subscription("sub-active-null", "active", period_expiry=None),
        _subscription("sub-already-expired", "expired", period_expiry=past),
        _subscription("sub-cancelled", "cancelled", period_expiry=past),
        _subscription("sub-refunded", "refunded", period_expiry=past),
        _subscription("sub-pending", "pending", period_expiry=past),
        _subscription("sub-payment-failed", "payment_failed", period_expiry=past),
    ]


ELIGIBLE_IDS = {"sub-active-past", "sub-suspended-past", "sub-active-at-boundary"}
INELIGIBLE_IDS = {
    "sub-active-future",
    "sub-active-null",
    "sub-already-expired",
    "sub-cancelled",
    "sub-refunded",
    "sub-pending",
    "sub-payment-failed",
}


def _run_sweep(client: FakeSupabase, *, now: datetime = NOW, **kwargs: Any) -> SweepOutcome:
    writer = kwargs.pop("audit_writer", None) or _RecordingAuditWriter()
    return _run_coroutine(
        sweep(supabase=client, now=now, audit_writer=writer, **kwargs)
    )


@pytest.fixture(autouse=True)
def _clean_metrics() -> Any:
    """Each test starts with no ``last_run_at``, so its assertions are about its own pass."""
    sweep_module.reset_metrics()
    yield
    sweep_module.reset_metrics()


# ══════════════════════════════════════════════════════════════════════════
# 1. Only the eligible rows transition
# ══════════════════════════════════════════════════════════════════════════


def test_only_eligible_rows_transition() -> None:
    """The predicate's three clauses, exercised together over one mixed population.

    Requirement 11.8: "every Subscription whose expiry is at or before the current UTC time and
    whose Subscription_State is ACTIVE or SUSPENDED". The boundary row (``period_expiry == now``)
    is included, matching ``entitlement_resolver``'s ``now >= period_expiry`` - the two
    comparisons must agree or P-11 and P-13 would contradict each other.
    """
    client = FakeSupabase(subscriptions=_mixed_population())
    before = client.statuses()

    outcome = _run_sweep(client)

    assert outcome.ok, outcome.failures
    assert {r.subscription_id for r in outcome.expired} == ELIGIBLE_IDS
    assert outcome.expired_count == 3

    after = client.statuses()
    for sub_id in ELIGIBLE_IDS:
        assert after[sub_id] == EXPIRED_STATUS_TEXT, f"{sub_id} should have expired"
    for sub_id in INELIGIBLE_IDS:
        assert after[sub_id] == before[sub_id], f"{sub_id} must not have been touched"


def test_the_from_state_recorded_is_the_row_s_actual_prior_state() -> None:
    """``from_state`` is read from the row, never inferred - Requirement 11.12's prior value.

    ``sub-suspended-past`` is the case that catches an inference: a sweep that assumed every
    expiry came from ``active`` would write a history row claiming a transition that never
    happened.
    """
    client = FakeSupabase(subscriptions=_mixed_population())
    outcome = _run_sweep(client)

    by_id = {r.subscription_id: r for r in outcome.expired}
    assert by_id["sub-active-past"].from_state == "active"
    assert by_id["sub-suspended-past"].from_state == "suspended"
    assert by_id["sub-active-at-boundary"].from_state == "active"

    history = {r["subscription_id"]: r for r in client.rows("library_subscription_transitions")}
    assert history["sub-suspended-past"]["from_state"] == "suspended"
    assert history["sub-active-past"]["from_state"] == "active"


def test_there_is_exactly_one_update_statement_against_library_subscriptions() -> None:
    """One write statement per pass, however many rows expire (Requirement 11.8).

    Three rows expire here; if the sweep had degenerated into one UPDATE per row this count
    would be three, and the index that supports it (``idx_lib_subs_expiry``) would be scanned
    once per subscription instead of once per pass.
    """
    client = FakeSupabase(subscriptions=_mixed_population())
    _run_sweep(client)

    updates = [
        q
        for q in client.statements
        if q.op == "update" and q.table_name == "library_subscriptions"
    ]
    assert len(updates) == 1, f"expected one UPDATE, saw {len(updates)}"

    # And it carries the whole predicate itself, not only the id narrowing.
    kinds = {(kind, column) for kind, column, _ in updates[0].filters}
    assert ("lte", "period_expiry") in kinds
    assert ("not.is", "period_expiry") in kinds
    assert ("in", "status") in kinds
    status_filter = next(
        values for kind, column, values in updates[0].filters
        if kind == "in" and column == "status"
    )
    assert sorted(status_filter) == sorted(EXPIRABLE_STATUS_TEXTS)


# ══════════════════════════════════════════════════════════════════════════
# 2-4. The rows that must be left alone, each named
# ══════════════════════════════════════════════════════════════════════════


def test_an_already_expired_row_is_untouched() -> None:
    """The clause that makes P-13 true: ``'expired'`` is not in the status predicate.

    A row that already reads ``expired`` produces no update, no history row, no audit entry and
    no enforcement write - so its ``updated_at`` is unchanged too, which is how "untouched" is
    told apart from "re-written with the same value".
    """
    row = _subscription("sub-already-expired", "expired", period_expiry=PAST)
    client = FakeSupabase(subscriptions=[row])

    outcome = _run_sweep(client)

    assert outcome.expired_count == 0
    assert client.rows("library_subscriptions") == [row]
    assert client.rows("library_subscription_transitions") == []
    assert ("update", "library_subscriptions") not in client.ops()


def test_a_null_period_expiry_row_is_untouched() -> None:
    """No period means no expiry. ``NULL <= now`` is unknown, and the sweep says so explicitly."""
    row = _subscription("sub-active-null", "active", period_expiry=None)
    client = FakeSupabase(subscriptions=[row])

    outcome = _run_sweep(client)

    assert outcome.expired_count == 0
    assert client.rows("library_subscriptions") == [row]
    assert client.rows("library_subscription_transitions") == []


@pytest.mark.parametrize("status", ["cancelled", "refunded"])
def test_a_cancelled_or_refunded_row_is_untouched(status: str) -> None:
    """Neither has an edge to ``EXPIRED`` (Requirement 11.2), so neither may be swept.

    ``cancelled`` matters for a second reason: Requirement 11.9 keeps entitlement to the
    unchanged expiry after a cancellation, and relabelling the row would misreport that in the
    Strategies page's subscription state.
    """
    assert not can_transition(status, SubscriptionState.EXPIRED)

    row = _subscription(f"sub-{status}", status, period_expiry=PAST)
    client = FakeSupabase(subscriptions=[row])

    outcome = _run_sweep(client)

    assert outcome.expired_count == 0
    assert client.rows("library_subscriptions") == [row]
    assert client.rows("library_subscription_transitions") == []


# ══════════════════════════════════════════════════════════════════════════
# 5. One transition row and one audit entry per expired Subscription
# ══════════════════════════════════════════════════════════════════════════


def test_one_transition_row_and_one_audit_entry_per_expired_subscription() -> None:
    """Requirements 11.12 and 26.2, counted rather than assumed."""
    client = FakeSupabase(subscriptions=_mixed_population())
    writer = _RecordingAuditWriter()

    outcome = _run_sweep(client, audit_writer=writer)

    assert outcome.expired_count == 3
    assert outcome.transitions_written == 3
    assert outcome.audit_entries_written == 3

    history = client.rows("library_subscription_transitions")
    assert len(history) == 3
    assert {r["subscription_id"] for r in history} == ELIGIBLE_IDS
    assert len(writer.calls) == 3
    assert {c["resource_id"] for c in writer.calls} == ELIGIBLE_IDS


def test_the_transition_row_carries_requirement_11_12_s_facts() -> None:
    """Prior value, new value, cause, timestamp - and no invented period movement.

    ``prior_period_expiry`` and ``new_period_expiry`` are absent because the sweep moves no
    period; 008 section 3's own comment makes "the period did not move" the meaning of NULL in
    both, so writing the unchanged expiry into ``prior_period_expiry`` would claim an extension
    that did not happen. ``actor_id`` is absent because no identity acted.
    """
    client = FakeSupabase(
        subscriptions=[_subscription("sub-1", "active", period_expiry=PAST)]
    )
    _run_sweep(client)

    history = client.rows("library_subscription_transitions")
    assert len(history) == 1
    row = history[0]
    assert row["subscription_id"] == "sub-1"
    assert row["user_id"] == PURCHASER
    assert row["from_state"] == "active"
    assert row["to_state"] == EXPIRED_STATUS_TEXT
    assert row["cause"] == sweep_module.TRANSITION_CAUSE == "expiry_sweep"
    assert _as_instant(row["transitioned_at"]) == NOW
    assert "prior_period_expiry" not in row
    assert "new_period_expiry" not in row
    assert "actor_id" not in row


def test_the_audit_entry_names_the_act_and_carries_the_prior_and_new_value() -> None:
    """One ``SUBSCRIPTION_EXPIRED`` record per expiry, with Requirement 11.12's five facts."""
    client = FakeSupabase(
        subscriptions=[_subscription("sub-1", "suspended", period_expiry=PAST)]
    )
    writer = _RecordingAuditWriter()

    _run_sweep(client, audit_writer=writer)

    assert len(writer.calls) == 1
    call = writer.calls[0]
    assert call["resource_id"] == "sub-1"
    assert call["before"] == "suspended"
    assert call["after"] == EXPIRED_STATUS_TEXT
    assert sweep_module.AUDIT_REASON_CODE == "SUBSCRIPTION_EXPIRED"
    assert sweep_module.AUDIT_REASON_CODE in call["reason"]
    assert call["metadata"]["audited_act"] == "SUBSCRIPTION_EXPIRED"
    assert call["metadata"]["cause"] == "expiry_sweep"
    assert _as_instant(call["metadata"]["period_expiry"]) == PAST
    # The one statement in the trail that matters during an incident: the label moved, the
    # access decision did not - it was already made by ``period_expiry`` (Requirement 11.7).
    assert call["metadata"]["entitlement_decided_by"] == "period_expiry"


def test_the_audit_action_is_the_existing_requirement_26_2_member() -> None:
    """No second audit facility and no new enum member (Requirement 26.2).

    With no injected writer the sweep resolves the shared ``StrategyAuditLogger`` and the
    ``MARKETPLACE_SUBSCRIPTION_TRANSITIONED`` action Requirement 26.2 already allocates to a
    Subscription_State transition. Patching only the logger getter keeps this a test of *which
    member the sweep chose*, with the real ``StrategyAuditAction`` import doing the work.
    """
    from backend_app.core import audit_trail

    seen: List[Any] = []

    class _Logger:
        async def record_or_raise(self, action: Any, **kwargs: Any) -> Any:
            seen.append(action)
            return {"audit_id": "A-1"}

    original = audit_trail.get_strategy_audit_logger
    audit_trail.get_strategy_audit_logger = lambda: _Logger()  # type: ignore[assignment]
    try:
        client = FakeSupabase(
            subscriptions=[_subscription("sub-1", "active", period_expiry=PAST)]
        )
        _run_coroutine(sweep(supabase=client, now=NOW))
    finally:
        audit_trail.get_strategy_audit_logger = original  # type: ignore[assignment]

    assert seen == [audit_trail.StrategyAuditAction.MARKETPLACE_SUBSCRIPTION_TRANSITIONED]


# ══════════════════════════════════════════════════════════════════════════
# 6. Running it twice changes nothing the second time
# ══════════════════════════════════════════════════════════════════════════


def test_running_the_sweep_twice_changes_nothing_the_second_time() -> None:
    """The named-example half of property P-13 (task 20.2 asserts the property).

    The second pass issues its candidate read, finds no row satisfying the predicate, and stops:
    no UPDATE, no history row, no audit entry, no enforcement write. Every table is byte-for-byte
    what the first pass left behind.
    """
    client = FakeSupabase(
        subscriptions=_mixed_population(),
        deployments=[
            {
                "id": "dep-1",
                "user_id": PURCHASER,
                "marketplace_listing_id": LISTING,
                "status": "running",
            }
        ],
    )

    first = _run_sweep(client)
    after_first = client.snapshot()
    statements_after_first = len(client.statements)

    second = _run_sweep(client)

    assert first.expired_count == 3
    assert second.expired_count == 0
    assert second.transitions_written == 0
    assert second.audit_entries_written == 0
    assert client.snapshot() == after_first, "the second pass changed persisted state"

    # And it cost exactly one statement: the candidate read that found nothing.
    second_pass = client.statements[statements_after_first:]
    assert [(q.op, q.table_name) for q in second_pass] == [
        ("select", "library_subscriptions")
    ]


def test_a_third_and_fourth_pass_are_also_no_ops() -> None:
    """``n >= 1`` repetitions, not just two - the same set of states as running it once."""
    client = FakeSupabase(subscriptions=_mixed_population())
    _run_sweep(client)
    reference = client.snapshot()

    for _ in range(3):
        outcome = _run_sweep(client)
        assert outcome.expired_count == 0
        assert client.snapshot() == reference


# ══════════════════════════════════════════════════════════════════════════
# 7. A failure is surfaced, never swallowed
# ══════════════════════════════════════════════════════════════════════════


def test_a_failed_candidate_read_is_raised_and_nothing_is_written() -> None:
    """A broken read is not "nothing to expire" (Requirements 26.5, 30.5).

    Answering zero for a failed read is how a sweep silently stops sweeping: the count looks
    normal, the log looks quiet, and subscriptions drift for as long as nobody notices.
    """
    client = FakeSupabase(
        subscriptions=_mixed_population(),
        raise_on={("select", "library_subscriptions")},
    )

    with pytest.raises(ExpirySweepFailed) as caught:
        _run_sweep(client)

    assert caught.value.stage == "candidate_read"
    assert client.rows("library_subscription_transitions") == []
    assert set(client.statuses().values()) != {EXPIRED_STATUS_TEXT}


def test_a_postgrest_error_envelope_is_a_failure_not_an_empty_result() -> None:
    """A response that "completed" with an ``error`` member did not complete."""
    client = FakeSupabase(
        subscriptions=_mixed_population(),
        error_on={("select", "library_subscriptions")},
    )

    with pytest.raises(ExpirySweepFailed):
        _run_sweep(client)


def test_a_failed_expiry_update_is_raised() -> None:
    """The write failed, so nothing transitioned and no history was invented for it."""
    client = FakeSupabase(
        subscriptions=_mixed_population(),
        raise_on={("update", "library_subscriptions")},
    )

    with pytest.raises(ExpirySweepFailed) as caught:
        _run_sweep(client)

    assert caught.value.stage == "expiry_update"
    assert client.rows("library_subscription_transitions") == []


def test_a_failed_transition_insert_is_surfaced_with_the_rows_that_did_transition() -> None:
    """The state change committed; the history row did not. That gap must be reported.

    The exception carries the partial outcome, because an operator needs to know that the rows
    *did* move even though the history did not - "the sweep failed" and "nothing happened" are
    different incidents.
    """
    client = FakeSupabase(
        subscriptions=_mixed_population(),
        raise_on={("insert", "library_subscription_transitions")},
    )

    with pytest.raises(ExpirySweepFailed) as caught:
        _run_sweep(client)

    failure = caught.value
    assert failure.stage == "transition_insert"
    assert failure.outcome is not None
    assert failure.outcome.expired_count == 3
    assert failure.outcome.transitions_written == 0
    # Every expired row is attributed, so one failure does not hide the other two.
    assert {f.subscription_id for f in failure.failures if f.stage == "transition_insert"} == (
        ELIGIBLE_IDS
    )


def test_a_failed_audit_write_is_surfaced_and_the_other_rows_still_get_theirs() -> None:
    """One row's audit failure must not cost the others theirs (Requirement 26.5)."""
    client = FakeSupabase(subscriptions=_mixed_population())
    writer = _RecordingAuditWriter(fail_for={"sub-active-past"})

    with pytest.raises(ExpirySweepFailed) as caught:
        _run_sweep(client, audit_writer=writer)

    failure = caught.value
    assert [f.subscription_id for f in failure.failures] == ["sub-active-past"]
    assert failure.outcome is not None
    assert failure.outcome.audit_entries_written == 2
    assert {c["resource_id"] for c in writer.calls} == ELIGIBLE_IDS - {"sub-active-past"}
    # The other two rows' history was still written: the loop completed rather than bailing.
    assert failure.outcome.transitions_written == 3


def test_last_run_at_does_not_advance_for_a_failed_pass() -> None:
    """The health-check input goes stale on failure, which is the point of having one.

    Stamping ``last_run_at`` on every attempt would make a permanently broken sweep look
    permanently healthy, and the operator would hear about the drift from a subscriber.
    """
    assert sweep_module.last_run_at() is None

    ok_client = FakeSupabase(subscriptions=_mixed_population())
    _run_sweep(ok_client)
    stamped = sweep_module.last_run_at()
    assert stamped == NOW

    broken = FakeSupabase(
        subscriptions=[_subscription("sub-2", "active", period_expiry=PAST)],
        raise_on={("update", "library_subscriptions")},
    )
    with pytest.raises(ExpirySweepFailed):
        _run_sweep(broken, now=NOW + timedelta(minutes=1))

    assert sweep_module.last_run_at() == stamped, (
        "a failed pass must not advance the health-check input"
    )


def test_an_empty_pass_still_advances_last_run_at() -> None:
    """Nothing to expire is a *successful* pass, and the probe must not read it as a stall."""
    client = FakeSupabase(
        subscriptions=[_subscription("sub-1", "active", period_expiry=FUTURE)]
    )
    outcome = _run_sweep(client)

    assert outcome.expired_count == 0
    assert outcome.ok
    assert sweep_module.last_run_at() == NOW
    assert sweep_module.metrics_snapshot()[sweep_module.METRIC_LAST_RUN_AT] == NOW.isoformat()


def test_the_module_never_swallows_an_exception_into_a_verdict() -> None:
    """Structural: no bare ``except`` and no ``except`` that returns instead of raising.

    Requirement 26.5's "SHALL NOT swallow an exception on such a path without a log record and a
    defined outcome", read as source. Every ``except`` in the module is followed by a ``raise``
    or by appending a :class:`SweepFailure` that ``sweep`` then raises on.
    """
    import ast
    import inspect

    # The one documented exemption. ``_coerce_optional_instant`` turns an unparseable *copy* of
    # a timestamp into ``None``. It is on no decision path: the database evaluated
    # ``period_expiry <= now``, and this value is carried only into the audit entry's metadata.
    # Raising there would abandon a correct expiry because its timestamp string was odd, which
    # would be the worse outcome. Nothing else in the module may swallow.
    PARSE_ONLY_HELPERS = {"_coerce_optional_instant"}

    source = inspect.getsource(sweep_module)
    tree = ast.parse(source)
    problems: List[str] = []
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(func):
            if not isinstance(node, ast.ExceptHandler):
                continue
            if node.type is None:
                problems.append(f"bare except in {func.name} at line {node.lineno}")
                continue
            if func.name in PARSE_ONLY_HELPERS:
                continue
            reraises = any(isinstance(n, ast.Raise) for n in ast.walk(node))
            records = any(
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name)
                and n.func.id == "SweepFailure"
                for n in ast.walk(node)
            )
            if not (reraises or records):
                problems.append(
                    f"except in {func.name} at line {node.lineno} neither re-raises nor "
                    f"records a SweepFailure"
                )
    assert not problems, "; ".join(problems)


# ══════════════════════════════════════════════════════════════════════════
# 8. The sweep cannot produce an ``active`` row
# ══════════════════════════════════════════════════════════════════════════


def test_the_target_state_is_expired_and_only_expired() -> None:
    """The single place the target is named, and it is not ``ACTIVE`` (Requirement 11.6)."""
    assert SWEEP_TARGET_STATE is SubscriptionState.EXPIRED
    assert SWEEP_TARGET_STATE is not SubscriptionState.ACTIVE
    assert EXPIRED_STATUS_TEXT == STATUS_TEXT_FOR_STATE[SubscriptionState.EXPIRED] == "expired"
    assert EXPIRED_STATUS_TEXT != STATUS_TEXT_FOR_STATE[SubscriptionState.ACTIVE]
    # ``active`` and ``suspended`` are the SOURCE states (Requirement 11.2's two edges into
    # EXPIRED); the target is never among them, which is what makes a second pass a no-op.
    assert sorted(EXPIRABLE_STATUS_TEXTS) == ["active", "suspended"]
    assert EXPIRED_STATUS_TEXT not in EXPIRABLE_STATUS_TEXTS


def test_the_update_payload_is_only_the_status_and_the_touch_timestamp() -> None:
    """Two columns, and neither of them is one the Entitlement_Resolver decides access from.

    A sweep that could write ``period_expiry`` could extend access; a sweep that could write
    ``expires_at`` could make a monthly Subscription perpetual, because
    ``check_deployment_permission`` reads a null there as *perpetual*. It writes neither.
    """
    client = FakeSupabase(subscriptions=_mixed_population())
    _run_sweep(client)

    payloads = [
        q.payload
        for q in client.statements
        if q.op == "update" and q.table_name == "library_subscriptions"
    ]
    assert len(payloads) == 1
    payload = payloads[0] or {}
    assert set(payload) == {"status", "updated_at"}
    assert payload["status"] == EXPIRED_STATUS_TEXT
    for forbidden in ("period_expiry", "period_start", "expires_at", "renewal_enabled"):
        assert forbidden not in payload


def test_no_sweep_over_any_population_ever_produces_an_active_row() -> None:
    """Exhaustive over the seven states x three expiry positions: ``active`` is never written.

    The strongest form of the claim that is checkable without a property test: for every
    Subscription_State and every position of ``period_expiry`` relative to ``now``, run the
    sweep and assert no row's status is ``active`` unless it already was, and no history row
    claims ``active`` as its target.
    """
    active_text = STATUS_TEXT_FOR_STATE[SubscriptionState.ACTIVE]
    rows: List[Dict[str, Any]] = []
    for state in SubscriptionState:
        text = STATUS_TEXT_FOR_STATE[state]
        for label, expiry in (("past", PAST), ("now", NOW), ("future", FUTURE), ("null", None)):
            rows.append(_subscription(f"sub-{text}-{label}", text, period_expiry=expiry))

    client = FakeSupabase(subscriptions=rows)
    before = client.statuses()

    outcome = _run_sweep(client)

    after = client.statuses()
    for sub_id, status in after.items():
        if status == active_text:
            assert before[sub_id] == active_text, (
                f"{sub_id} became active, which the sweep may never do"
            )
    for record in outcome.expired:
        assert record.to_state == EXPIRED_STATUS_TEXT
    for history_row in client.rows("library_subscription_transitions"):
        assert history_row["to_state"] == EXPIRED_STATUS_TEXT


def test_an_expired_subscription_value_cannot_claim_an_active_target() -> None:
    """The value type refuses it, so a caller cannot construct the claim either."""
    with pytest.raises(ValueError):
        ExpiredSubscription(
            subscription_id="sub-1",
            user_id=PURCHASER,
            listing_id=LISTING,
            from_state="active",
            period_expiry=PAST,
            to_state=STATUS_TEXT_FOR_STATE[SubscriptionState.ACTIVE],
        )


def test_an_expired_subscription_value_cannot_claim_an_unexpirable_source() -> None:
    """``cancelled -> expired`` is not a permitted edge, so the record cannot be built."""
    with pytest.raises(ValueError):
        ExpiredSubscription(
            subscription_id="sub-1",
            user_id=PURCHASER,
            listing_id=LISTING,
            from_state="cancelled",
            period_expiry=PAST,
        )


# ══════════════════════════════════════════════════════════════════════════
# Requirement 11.15 - what is stopped, and what is honestly deferred
# ══════════════════════════════════════════════════════════════════════════


def test_running_deployments_of_the_expired_listing_are_stopped_for_that_purchaser_only() -> None:
    """Requirement 11.15, scoped by BOTH the Listing and the purchaser.

    Another subscriber's still-paid deployment of the same Listing, and this purchaser's
    deployment of a different Listing, are different rows and stay running. Scoping by one
    column would stop one of them.
    """
    client = FakeSupabase(
        subscriptions=[_subscription("sub-1", "active", period_expiry=PAST)],
        deployments=[
            {
                "id": "dep-mine-running",
                "user_id": PURCHASER,
                "marketplace_listing_id": LISTING,
                "status": "running",
            },
            {
                "id": "dep-mine-deploying",
                "user_id": PURCHASER,
                "marketplace_listing_id": LISTING,
                "status": "deploying",
            },
            {
                "id": "dep-mine-already-stopped",
                "user_id": PURCHASER,
                "marketplace_listing_id": LISTING,
                "status": "stopped",
            },
            {
                "id": "dep-other-user",
                "user_id": "someone-else",
                "marketplace_listing_id": LISTING,
                "status": "running",
            },
            {
                "id": "dep-other-listing",
                "user_id": PURCHASER,
                "marketplace_listing_id": "listing-2",
                "status": "running",
            },
        ],
    )

    outcome = _run_sweep(client)

    assert outcome.deployments_stopped == 2
    by_id = {r["id"]: r for r in client.rows("strategy_deployments")}
    assert by_id["dep-mine-running"]["status"] == "stopped"
    assert _as_instant(by_id["dep-mine-running"]["stopped_at"]) == NOW
    assert by_id["dep-mine-deploying"]["status"] == "stopped"
    assert by_id["dep-other-user"]["status"] == "running"
    assert by_id["dep-other-listing"]["status"] == "running"
    # An already-stopped deployment is not re-stopped, so no spurious ``stopped_at`` appears.
    assert "stopped_at" not in by_id["dep-mine-already-stopped"]


def test_paper_sessions_are_stopped_only_from_the_two_states_that_permit_it() -> None:
    """``RUNNING`` and ``PAUSED`` only - the edges ``paper_session_allowed_transitions`` has.

    ``CREATED -> STOPPED`` is not a permitted Paper_Session edge (009 section 2), so including a
    ``CREATED`` session would earn a 23514 from ``trg_paper_session_guard`` rather than stopping
    anything. A ``CREATED`` session is not running, so it does not need stopping.
    """
    client = FakeSupabase(
        subscriptions=[_subscription("sub-1", "active", period_expiry=PAST)],
        paper_sessions=[
            {"id": "ps-run", "user_id": PURCHASER, "listing_id": LISTING,
             "session_state": "RUNNING"},
            {"id": "ps-paused", "user_id": PURCHASER, "listing_id": LISTING,
             "session_state": "PAUSED"},
            {"id": "ps-created", "user_id": PURCHASER, "listing_id": LISTING,
             "session_state": "CREATED"},
            {"id": "ps-stopped", "user_id": PURCHASER, "listing_id": LISTING,
             "session_state": "STOPPED"},
            {"id": "ps-other", "user_id": "someone-else", "listing_id": LISTING,
             "session_state": "RUNNING"},
        ],
    )

    outcome = _run_sweep(client)

    assert outcome.paper_sessions_stopped == 2
    by_id = {r["id"]: r for r in client.rows("paper_sessions")}
    assert by_id["ps-run"]["session_state"] == "STOPPED"
    assert by_id["ps-paused"]["session_state"] == "STOPPED"
    assert by_id["ps-created"]["session_state"] == "CREATED"
    assert by_id["ps-other"]["session_state"] == "RUNNING"


def test_the_paper_session_runtime_stop_is_declared_deferred_rather_than_claimed() -> None:
    """The outcome says out loud that the runtime teardown is task 27.1's.

    There is no Paper_Session_Service yet - nothing can start a session, so nothing can be
    stopped at runtime. The sweep writes the persisted ``session_state`` (real) and flags the
    runtime action as deferred (honest) rather than reporting a stop it did not perform.
    """
    client = FakeSupabase(
        subscriptions=[_subscription("sub-1", "active", period_expiry=PAST)]
    )
    outcome = _run_sweep(client)

    assert outcome.paper_runtime_stop_deferred is True
    assert outcome.paper_sessions_stopped == 0


def test_a_failed_enforcement_update_is_surfaced_not_swallowed() -> None:
    """A deployment that could not be stopped is a Requirement 11.15 breach and must be loud."""
    client = FakeSupabase(
        subscriptions=[_subscription("sub-1", "active", period_expiry=PAST)],
        deployments=[
            {
                "id": "dep-1",
                "user_id": PURCHASER,
                "marketplace_listing_id": LISTING,
                "status": "running",
            }
        ],
        raise_on={("update", "strategy_deployments")},
    )

    with pytest.raises(ExpirySweepFailed) as caught:
        _run_sweep(client)

    assert any(f.stage == "stop_deployments" for f in caught.value.failures)
    assert caught.value.outcome is not None
    assert caught.value.outcome.expired_count == 1


# ══════════════════════════════════════════════════════════════════════════
# The interval, the index, the contract, and the sweep-independence statement
# ══════════════════════════════════════════════════════════════════════════


def test_the_hosting_interval_is_thirty_seconds_and_the_worker_reads_it_from_here() -> None:
    """Requirement 11.8's "no greater than 60 seconds", with a full interval of margin."""
    from backend_app.workers.marketplace_expiry_worker import MarketplaceExpiryWorker

    assert SWEEP_INTERVAL_SECONDS == 30.0
    assert SWEEP_INTERVAL_SECONDS <= 60.0

    worker = MarketplaceExpiryWorker(supabase=FakeSupabase(), worker_name="test-expiry")
    assert worker.poll_interval == SWEEP_INTERVAL_SECONDS
    # Not user-scoped: gating the thing that records lapses on a subscription check would be
    # circular. See the worker's module docstring.
    assert worker.requires_subscription_check is False


def test_the_worker_iteration_sweeps_and_surfaces_a_failure() -> None:
    """The worker is a timer: it calls the sweep at the wall clock and does not catch a failure.

    The population is anchored to ``datetime.now(timezone.utc)`` rather than to :data:`NOW`,
    because the worker passes **no** ``now`` - reading the real clock is the worker's entire
    contribution, and ``sweep``'s ``now`` parameter exists for the tests that need a fixed
    boundary, not for the worker. Handing a wall-clock sweep a population anchored to a fixed
    past ``NOW`` would make ``sub-active-future``'s expiry (``NOW + 10 days``) already past, so
    it would expire correctly and the test would be asserting against a clock mismatch of its
    own making rather than against the worker.

    The exact-boundary case (``period_expiry == now`` to the microsecond) is pinned by
    ``test_only_eligible_rows_transition``, which injects ``now`` and can therefore be exact.
    Here the boundary row is merely at-or-just-before the sweep's instant, which is the same side
    of ``<=``.
    """
    from backend_app.workers.marketplace_expiry_worker import MarketplaceExpiryWorker

    anchor = datetime.now(timezone.utc)
    client = FakeSupabase(subscriptions=_mixed_population(anchor))
    before = client.statuses()
    worker = MarketplaceExpiryWorker(supabase=client, worker_name="test-expiry")

    writer = _RecordingAuditWriter()
    original = sweep_module.sweep

    async def _sweep_with_writer(**kwargs: Any) -> Any:
        kwargs.setdefault("audit_writer", writer)
        return await original(**kwargs)

    sweep_module.sweep = _sweep_with_writer  # type: ignore[assignment]
    try:
        _run_coroutine(worker.process_iteration())
        assert worker.last_outcome is not None
        assert worker.last_outcome.ok, worker.last_outcome.failures
        assert {r.subscription_id for r in worker.last_outcome.expired} == ELIGIBLE_IDS
        assert worker.last_outcome.expired_count == 3

        # The worker swept at the real instant, not at some injected one: this is what makes the
        # anchoring above necessary, so it is asserted rather than assumed.
        assert worker.last_outcome.swept_at is not None
        assert worker.last_outcome.swept_at >= anchor

        after = client.statuses()
        for sub_id in INELIGIBLE_IDS:
            assert after[sub_id] == before[sub_id], f"{sub_id} must not have been touched"

        broken = FakeSupabase(
            subscriptions=[
                _subscription(
                    "sub-x", "active", period_expiry=anchor - timedelta(minutes=5)
                )
            ],
            raise_on={("select", "library_subscriptions")},
        )
        broken_worker = MarketplaceExpiryWorker(supabase=broken, worker_name="test-expiry-2")
        with pytest.raises(ExpirySweepFailed):
            _run_coroutine(broken_worker.process_iteration())
    finally:
        sweep_module.sweep = original  # type: ignore[assignment]


def test_last_run_at_is_reachable_as_a_health_check_input_through_the_worker() -> None:
    """``marketplace.expiry_sweep.last_run_at`` must be readable by a probe, not just internally.

    ``last_run_at()`` and ``metrics_snapshot()`` are asserted elsewhere; what this pins is the
    surface a health endpoint actually calls. ``health_details()`` must carry the metric under
    its ``design.md`` name and must carry ``interval_seconds`` beside it, because "stale" is only
    meaningful relative to the interval - a probe that had to guess it would either alarm on
    every first pass or never alarm at all.
    """
    from backend_app.workers.marketplace_expiry_worker import MarketplaceExpiryWorker

    worker = MarketplaceExpiryWorker(supabase=FakeSupabase(), worker_name="test-expiry-health")

    # Before any sweep the probe gets a truthful ``None`` rather than a missing key.
    details = worker.health_details()
    assert sweep_module.METRIC_LAST_RUN_AT in details
    assert details[sweep_module.METRIC_LAST_RUN_AT] is None
    assert details["interval_seconds"] == SWEEP_INTERVAL_SECONDS
    for metric in (
        sweep_module.METRIC_DURATION_MS,
        sweep_module.METRIC_TRANSITIONS,
        sweep_module.METRIC_ERRORS,
    ):
        assert metric in details

    # A successful pass advances it, so the probe's staleness comparison has something to read.
    _run_sweep(FakeSupabase(subscriptions=_mixed_population()))
    assert worker.health_details()[sweep_module.METRIC_LAST_RUN_AT] == NOW.isoformat()

    # A failed pass does not, which is what makes a permanently broken sweep look broken.
    with pytest.raises(ExpirySweepFailed):
        _run_sweep(
            FakeSupabase(
                subscriptions=_mixed_population(),
                raise_on={("select", "library_subscriptions")},
            )
        )
    assert worker.health_details()[sweep_module.METRIC_LAST_RUN_AT] == NOW.isoformat()
    assert worker.health_details()[sweep_module.METRIC_ERRORS] >= 1


def test_the_sweep_predicate_matches_the_partial_index_that_supports_it() -> None:
    """Requirement 24.4: ``idx_lib_subs_expiry`` is a partial index, and the predicate must fit it.

    The index is ``ON library_subscriptions (period_expiry) WHERE status IN
    ('active','suspended')``. A predicate whose status set differed from the index's ``WHERE``
    clause would leave the sweep on a sequential scan of the whole subscription table every 30
    seconds.
    """
    from pathlib import Path

    migration = (
        Path(__file__).resolve().parents[1]
        / "backend_app"
        / "migrations"
        / "008_marketplace_settlement.sql"
    )
    text = migration.read_text(encoding="utf-8", errors="replace")
    assert "idx_lib_subs_expiry" in text
    for status in EXPIRABLE_STATUS_TEXTS:
        assert f"'{status}'" in text
    assert sorted(EXPIRABLE_STATUS_TEXTS) == ["active", "suspended"]


def test_the_column_contract_entry_covers_every_table_the_sweep_touches() -> None:
    """The manifest is what keeps a mistyped column a red suite rather than a silent no-op."""
    entry = COLUMN_CONTRACT["expiry_sweep"]
    tables = entry["tables"]  # type: ignore[index]
    assert set(tables) == {
        "library_subscriptions",
        "library_subscription_transitions",
        "strategy_deployments",
        "paper_sessions",
    }
    for column in sweep_module.SWEEP_CANDIDATE_SELECT.split(","):
        assert column.strip() in tables["library_subscriptions"]
    assert "status" in tables["library_subscriptions"]
    assert "updated_at" in tables["library_subscriptions"]


def test_the_sweep_is_not_the_authority_on_entitlement() -> None:
    """Structural: nothing here can be, or become, an access decision (Requirement 11.7, P-11).

    Three checks, each about a different way the boundary could erode:
      * the sweep does not import ``entitlement_resolver``, so it cannot become a second opinion
        on a decision;
      * it exposes no ``resolve``-shaped or entitlement-shaped callable a route could reach for;
      * the wire code it carries is the shared catalogue's read-failure code, not an
        entitlement one - a sweep failure is never answered as "not subscribed" or "expired".
    """
    import inspect

    from backend_app.backend.marketplace import errors

    source = inspect.getsource(sweep_module)
    assert "import entitlement_resolver" not in source
    assert "from backend_app.backend.marketplace.entitlement_resolver" not in source

    exported = set(sweep_module.__all__)
    for forbidden in ("resolve", "is_entitled", "entitlement", "Entitlement"):
        assert forbidden not in exported

    assert ExpirySweepFailed.wire_code == errors.MARKETPLACE_READ_FAILED
    assert ExpirySweepFailed.wire_code != errors.MARKETPLACE_SUBSCRIPTION_EXPIRED


def test_the_resolver_refuses_a_lapsed_row_the_sweep_has_not_reached_yet() -> None:
    """The behavioural half of the same statement: a dead sweep does not extend access.

    ``sub-1`` still reads ``status = 'active'`` because this sweep never ran, and its
    ``period_expiry`` is in the past. The resolver refuses it anyway, with
    ``MARKETPLACE_SUBSCRIPTION_EXPIRED`` - which is what makes this module's failure a
    housekeeping incident rather than an authorisation one.
    """
    from backend_app.backend.marketplace import entitlement_resolver as er

    class _Client:
        def table(self, name: str) -> "_Client":
            self._table = name
            return self

        def select(self, *_a: Any, **_k: Any) -> "_Client":
            return self

        def eq(self, *_a: Any, **_k: Any) -> "_Client":
            return self

        def execute(self) -> Any:
            if self._table == "library_strategies":
                return _Resp(
                    [
                        {
                            "id": LISTING,
                            "author_id": "owner-1",
                            "source_strategy_id": "strategy-1",
                            "marketplace_submissions": [{"submission_state": "PUBLISHED"}],
                            "library_subscriptions": [
                                {
                                    "id": "sub-1",
                                    "user_id": PURCHASER,
                                    # The sweep has NOT run: the stored label still says active.
                                    "status": "active",
                                    "period_expiry": PAST.isoformat(),
                                }
                            ],
                        }
                    ]
                )
            return _Resp([{"id": "ver-1", "strategy_id": "strategy-1", "version": 1,
                           "is_draft": False}])

    entitlement = _run_coroutine(
        er.resolve(
            caller={"id": PURCHASER},
            listing_id=LISTING,
            supabase=_Client(),
            now=NOW,
        )
    )

    assert entitlement.entitling is False
    assert entitlement.reason is er.EntitlementReason.EXPIRED
    assert entitlement.wire_code == "MARKETPLACE_SUBSCRIPTION_EXPIRED"
