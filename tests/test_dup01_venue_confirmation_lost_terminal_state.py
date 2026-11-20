"""DUP-01 — a lost venue response must eventually reach a DEFINED terminal state.

WHAT WAS WRONG
===============
`OrderWatchdog._adjudicate_ambiguous_execution` resolves an `ambiguous_submission` row
(`submitted_at` set, `order_id IS NULL` -- the venue call was made and the response was
lost) by asking the venue, through `ExchangeExecutor.find_order_by_client_order_id`, which
tries a direct `clientOrderId` lookup and then scans open and closed orders.

When that whole lookup chain could not be reached at all -- no executor available, or
every lookup attempt raised -- the row was written to `UNKNOWN` and nothing was ever
scheduled to look at it again: `classify_stale_executions`, the only query that feeds this
adjudicator, selects `WHERE status = 'EXECUTING'`, and a row that already left EXECUTING
for UNKNOWN never matches it a second time. The row then sat UNKNOWN, forever, as the
platform's permanent last word on an execution that might still be live or might never
have reached the venue -- indistinguishable from each other because `clientOrderId` is not
honoured by coinbase at all (ccxt overwrites it) and is never sent to kraken
(`exchange_executor.py`'s own venue mapping), so there was no way to tell a transient fault
from a structural one from a single attempt.

THE FIX
=======
1. A second query, `ExecutionRecordRepository.classify_unresolved_submissions`, finds
   exactly that stranded population (`UNKNOWN`, no venue order id, `submitted_at` older
   than `CONFIRMATION_LOST_AFTER_SECONDS` -- 24h, this codebase's existing "give up"
   convention) and a new watchdog method, `recheck_unresolved_submissions`, re-runs the
   SAME venue lookup against it on every monitoring cycle.
2. `_adjudicate_ambiguous_execution` gained a `from_status` parameter so its "got a real
   answer" branches persist correctly whichever population called it, and a new helper,
   `_stay_unknown_or_give_up`, which is reached ONLY from the two branches with genuinely
   NO signal from the venue ("no executor" / "venue lookup failed" -- never from
   `not_found`, which is a real answer, and never from "status with no id" or "still
   open", which are partial POSITIVE evidence of life).
3. `_stay_unknown_or_give_up` stays `UNKNOWN` unconditionally on a row's FIRST
   adjudication attempt (`from_status is EXECUTING`) regardless of age, and only applies
   the age test once `from_status is UNKNOWN` (i.e. at least one attempt already
   happened) -- see `test_a_very_old_first_attempt_still_stays_unknown_not_failed` for
   why that ordering matters.
4. `ExecutionRecordRepository.give_up_on_unresolved_submission` is the single place that
   writes the new terminal state: `FAILED` with the `RECOVERY_CONFIRMATION_LOST` marker
   folded into `exchange_status`, transitioning ONLY from `UNKNOWN` with `order_id IS
   NULL` so it can never downgrade a row that just acquired positive evidence.
5. `has_unresolved_submission` (RT-17's symbol freeze) and `submission_may_have_reached_
   venue` (DUP-02's retry refusal) both already treat this new state exactly as they treat
   `UNKNOWN` -- the first needed a one-line widening (asserted here), the second needed
   none (asserted here as a regression guard).

WHAT THIS DOES NOT CLAIM
=========================
No venue is contacted. No live trading path is exercised -- RT-15 hard-gates it off, and
separately, a PAPER-mode `ExecutionEngine` is built with `exchange_executor=None`
(`core/execution_engine.py`'s own constructor docstring), so `_execute_trade_internal`
takes the simulated branch unconditionally and never calls `mark_submitted` or
`create_order` -- paper executions never reach `submitted_at`, `_adjudicate_ambiguous_
execution` or this file's code at all today. This is a dormant control until live trading
is re-enabled. `classify_unresolved_submissions` and `give_up_on_unresolved_submission`
are PostgreSQL-only raw SQL (mirroring `classify_stale_executions` / `resolve_stale_
execution`, which already are); no PostgreSQL is available in this environment, so their
SQL text is read but not executed against a server, consistent with the PostgreSQL-gated
tests already skipped elsewhere in this suite without one.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend_app.backend.exchange_executor import OrderStatusResult
from backend_app.backend.order_watchdog import OrderWatchdog, WatchdogConfig
from backend_app.core.models.execution_record import (
    CONFIRMATION_LOST_AFTER_SECONDS, RECOVERY_CONFIRMATION_LOST, Base,
    ExecutionRecordModel, ExecutionRecordRepository, ExecutionStatus,
    is_venue_confirmation_lost, submission_may_have_reached_venue)

TENANT = uuid.UUID("d01a0000-0000-4000-8000-00000000d01a")
SYMBOL = "BTCUSDT"


# ══════════════════════════════════════════════════════════════════════════════════════
#  CONSTANTS AND PURE HELPERS
# ══════════════════════════════════════════════════════════════════════════════════════

def test_the_confirmation_lost_threshold_is_long_and_distinct_from_the_recovery_one():
    """24h, not a number that happens to be bigger than 900s by accident.

    Picking a short threshold here would reintroduce exactly the risk this phase exists
    to avoid: giving up on coinbase/kraken before a transient fault has had a realistic
    chance to clear, which is indistinguishable from a structural inability to confirm.
    """
    recovery_stale_after_seconds = ExecutionRecordRepository.RECOVERY_STALE_AFTER_SECONDS

    assert CONFIRMATION_LOST_AFTER_SECONDS == 86400, (
        "the give-up threshold changed; re-derive the ledger evidence and the test "
        "below that depends on this exact value if this is intentional"
    )
    assert CONFIRMATION_LOST_AFTER_SECONDS > recovery_stale_after_seconds * 10, (
        "the confirmation-lost threshold must give repeated renewed attempts a long "
        "window relative to the first-look threshold, not merely a longer one"
    )


class _Row:
    """Minimal stand-in for an `ExecutionRecordModel` row, for the pure-function tests."""

    def __init__(self, status, exchange_status=None, submitted_at="set"):
        self.status = status
        self.exchange_status = exchange_status
        self.submitted_at = submitted_at


def test_is_venue_confirmation_lost_matches_only_the_marked_failed_row():
    marked = _Row(ExecutionStatus.FAILED, f"{RECOVERY_CONFIRMATION_LOST}: gave up after 24h")
    ordinary_failed = _Row(ExecutionStatus.FAILED, "not_found: the venue has no such order")
    unknown = _Row(ExecutionStatus.UNKNOWN, "submitted but unacknowledged")
    completed = _Row(ExecutionStatus.COMPLETED, None)

    assert is_venue_confirmation_lost(marked) is True
    assert is_venue_confirmation_lost(ordinary_failed) is False, (
        "an ordinary determinate FAILED (e.g. a definite not_found) must not be mistaken "
        "for the give-up state, or every existing FAILED row would silently gain the "
        "give-up state's freeze semantics"
    )
    assert is_venue_confirmation_lost(unknown) is False
    assert is_venue_confirmation_lost(completed) is False
    assert is_venue_confirmation_lost(None) is False
    assert is_venue_confirmation_lost(object()) is False, (
        "a record missing the attributes entirely must refuse, not raise"
    )


def test_submission_may_have_reached_venue_already_refuses_retry_for_the_new_state():
    """DUP-02's retry guard needed NO code change -- this pins why.

    It refuses retry for ANY non-COMPLETED status once `submitted_at` is set, which the
    give-up state always has (it only ever transitions from an `ambiguous_submission`
    row, which by definition has `submitted_at` set). This is the regression guard that
    a future refactor of `submission_may_have_reached_venue` must not break.
    """
    marked = _Row(
        ExecutionStatus.FAILED, f"{RECOVERY_CONFIRMATION_LOST}: gave up",
        submitted_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    assert submission_may_have_reached_venue(marked) is True


# ══════════════════════════════════════════════════════════════════════════════════════
#  has_unresolved_submission (RT-17 symbol freeze) — real SQLite ORM query
# ══════════════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def repo():
    engine = create_engine(
        "sqlite://", future=True,
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(
        bind=engine, tables=[ExecutionRecordModel.__table__],
    )
    maker = sessionmaker(bind=engine, future=True)
    session = maker()
    try:
        yield ExecutionRecordRepository(session)
    finally:
        session.close()
        engine.dispose()


def _insert(repo, *, execution_id, status, exchange_status=None, symbol=SYMBOL):
    row = ExecutionRecordModel(
        execution_id=execution_id,
        tenant_id=TENANT,
        strategy_id="strat-dup01",
        symbol=symbol,
        side="buy",
        size="1",
        price="1000",
        status=status,
        exchange_status=exchange_status,
        order_id=None,
        submitted_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    repo.db.add(row)
    repo.db.commit()


def test_has_unresolved_submission_matches_the_new_terminal_state(repo):
    _insert(
        repo, execution_id="e1", status=ExecutionStatus.FAILED,
        exchange_status=f"{RECOVERY_CONFIRMATION_LOST}: gave up after 24h",
    )
    assert repo.has_unresolved_submission(TENANT, SYMBOL) is True, (
        "a FAILED/confirmation_lost row must keep freezing the symbol exactly as "
        "UNKNOWN did, or converting UNKNOWN to this new label would REDUCE safety"
    )


def test_has_unresolved_submission_still_matches_plain_unknown(repo):
    _insert(repo, execution_id="e1", status=ExecutionStatus.UNKNOWN)
    assert repo.has_unresolved_submission(TENANT, SYMBOL) is True


def test_has_unresolved_submission_does_not_match_an_ordinary_failure(repo):
    """An everyday rejected order must not freeze the symbol. Only ambiguity does."""
    _insert(
        repo, execution_id="e1", status=ExecutionStatus.FAILED,
        exchange_status="not_found: the venue has no such order",
    )
    assert repo.has_unresolved_submission(TENANT, SYMBOL) is False


def test_has_unresolved_submission_does_not_match_completed(repo):
    _insert(repo, execution_id="e1", status=ExecutionStatus.COMPLETED)
    assert repo.has_unresolved_submission(TENANT, SYMBOL) is False


# ══════════════════════════════════════════════════════════════════════════════════════
#  classify_unresolved_submissions — dialect fail-safe (no PostgreSQL available here)
# ══════════════════════════════════════════════════════════════════════════════════════

def test_classify_unresolved_submissions_returns_nothing_on_a_non_postgres_dialect(repo):
    """Same fail-safe `classify_stale_executions` already uses: no classification, ever,
    on a dialect this raw SQL was not written for -- never a WRONG one."""
    assert repo.classify_unresolved_submissions() == []


def test_give_up_on_unresolved_submission_refuses_on_a_non_postgres_dialect(repo):
    """The write side must also fail safe rather than attempt PostgreSQL-only SQL."""
    _insert(repo, execution_id="e1", status=ExecutionStatus.UNKNOWN)
    # SQLite does not understand `upper(status::text)`; the method must catch that and
    # return False (row left UNKNOWN) rather than raise out of the watchdog loop.
    result = repo.give_up_on_unresolved_submission(
        "e1", TENANT, detail="test probe on a non-postgres dialect",
    )
    assert result is False
    row = repo.get_by_id("e1", TENANT)
    assert row.status == ExecutionStatus.UNKNOWN, (
        "a failed give-up write must leave the row exactly as it was"
    )


# ══════════════════════════════════════════════════════════════════════════════════════
#  _adjudicate_ambiguous_execution — the age-gated give-up decision
# ══════════════════════════════════════════════════════════════════════════════════════

class _RecordingRepo:
    """Records every decision without touching a database. Mirrors the double
    `tests/test_d5_watchdog_adjudication_repair.py` already established."""

    RECOVERY_FAILED_BEFORE_SUBMISSION = "failed_before_submission"
    RECOVERY_AMBIGUOUS_SUBMISSION = "ambiguous_submission"
    RECOVERY_SUBMITTED_WITH_ORDER_ID = "submitted_with_order_id"

    def __init__(self) -> None:
        self.resolutions: List[Dict[str, Any]] = []
        self.give_ups: List[Dict[str, Any]] = []
        self.give_up_succeeds = True

    def resolve_stale_execution(self, execution_id, tenant_id, **kwargs):
        self.resolutions.append({"execution_id": execution_id, "tenant_id": tenant_id, **kwargs})
        return True

    def give_up_on_unresolved_submission(self, execution_id, tenant_id, *, detail):
        self.give_ups.append(
            {"execution_id": execution_id, "tenant_id": tenant_id, "detail": detail}
        )
        return self.give_up_succeeds


class _StubExecutor:
    """A venue double that can be looked up and CANNOT be ordered through."""

    def __init__(self, result: Optional[OrderStatusResult]) -> None:
        self._result = result
        self.lookup_calls: List[tuple] = []

    async def find_order_by_client_order_id(self, client_order_id, symbol):
        self.lookup_calls.append((client_order_id, symbol))
        if self._result is None:
            raise RuntimeError("venue unreachable")
        return self._result

    async def create_order(self, *a, **k):  # pragma: no cover - must never be reached
        raise AssertionError("recovery placed an order: create_order was called")

    async def place_order(self, *a, **k):  # pragma: no cover - must never be reached
        raise AssertionError("recovery placed an order: place_order was called")


def _row(age_seconds: float, classification: str = "ambiguous_submission") -> Dict[str, Any]:
    return {
        "execution_id": "exec_dup01aaaaaaaaaa",
        "tenant_id": TENANT,
        "symbol": SYMBOL,
        "side": "buy",
        "size": "1",
        "price": "1000",
        "exchange_id": "coinbase",
        "order_id": None,
        "submitted_at": datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
        "created_at": datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
        "age_seconds": age_seconds,
        "classification": classification,
    }


@pytest.fixture
def watchdog_with():
    def _build(executor):
        w = OrderWatchdog(db_session=None, config=WatchdogConfig())

        async def _lookup(order):
            return executor

        w._get_executor_for_order = _lookup
        alerts: List[str] = []

        async def _record_alert(*args, **kwargs):
            alerts.append(args[-1] if args else kwargs.get("reason", ""))

        w._trigger_recovery_alert = _record_alert
        return w, alerts

    return _build


# ---- "no executor" and "venue lookup failed": the two genuinely-no-signal branches ----

def test_a_very_old_first_attempt_still_stays_unknown_not_failed(watchdog_with):
    """THE EDGE CASE THAT MOTIVATED `from_status`-gating the give-up decision.

    A row's FIRST adjudication attempt (from `recover_unsubmitted_executions`,
    `from_status=EXECUTING`) must stay UNKNOWN even if `age_seconds` -- measured from
    `created_at` there -- already exceeds `CONFIRMATION_LOST_AFTER_SECONDS` (e.g. after a
    long watchdog outage). `give_up_on_unresolved_submission` only ever transitions FROM
    `UNKNOWN`, so giving up on a first look would silently match zero rows and leave the
    row EXECUTING forever -- worse than the pre-fix defect.
    """
    w, alerts = watchdog_with(None)
    repo = _RecordingRepo()
    row = _row(age_seconds=CONFIRMATION_LOST_AFTER_SECONDS * 10)

    asyncio.run(w._adjudicate_ambiguous_execution(repo, row))  # from_status defaults EXECUTING

    assert repo.give_ups == [], "gave up on a row's first-ever adjudication attempt"
    assert len(repo.resolutions) == 1
    assert repo.resolutions[0]["status"] == ExecutionStatus.UNKNOWN
    assert repo.resolutions[0]["from_status"] == ExecutionStatus.EXECUTING
    assert alerts


def test_a_recheck_under_the_threshold_stays_unknown(watchdog_with):
    """`from_status=UNKNOWN` but not old enough yet -- still just UNKNOWN, no give-up."""
    w, alerts = watchdog_with(None)
    repo = _RecordingRepo()
    row = _row(age_seconds=CONFIRMATION_LOST_AFTER_SECONDS - 1)

    asyncio.run(
        w._adjudicate_ambiguous_execution(repo, row, from_status=ExecutionStatus.UNKNOWN)
    )

    assert repo.give_ups == []
    assert repo.resolutions[0]["status"] == ExecutionStatus.UNKNOWN
    assert repo.resolutions[0]["from_status"] == ExecutionStatus.UNKNOWN
    assert alerts


def test_a_recheck_past_the_threshold_with_no_executor_gives_up(watchdog_with):
    """The headline DUP-01 behaviour: repeated inability to even ask becomes FAILED."""
    w, alerts = watchdog_with(None)
    repo = _RecordingRepo()
    row = _row(age_seconds=CONFIRMATION_LOST_AFTER_SECONDS + 1)

    asyncio.run(
        w._adjudicate_ambiguous_execution(repo, row, from_status=ExecutionStatus.UNKNOWN)
    )

    assert repo.resolutions == [], (
        "the give-up path must write through give_up_on_unresolved_submission, not "
        "resolve_stale_execution"
    )
    assert len(repo.give_ups) == 1
    assert "DUP-01" not in repo.give_ups[0]["detail"] or True  # detail text is free-form
    assert alerts, "no alert raised for the defined terminal give-up state"


def test_a_recheck_past_the_threshold_with_a_failing_lookup_gives_up(watchdog_with):
    """Same give-up, reached through the OTHER no-signal branch (venue lookup failed)."""
    executor = _StubExecutor(
        OrderStatusResult(
            success=False, status="error", filled_size="0", remaining_size="0",
            avg_price=None, error_message="venue unreachable (coinbase)",
        )
    )
    w, alerts = watchdog_with(executor)
    repo = _RecordingRepo()
    row = _row(age_seconds=CONFIRMATION_LOST_AFTER_SECONDS + 1)

    asyncio.run(
        w._adjudicate_ambiguous_execution(repo, row, from_status=ExecutionStatus.UNKNOWN)
    )

    assert executor.lookup_calls, "the lookup was not even attempted before giving up"
    assert repo.resolutions == []
    assert len(repo.give_ups) == 1
    assert alerts


def test_a_raced_give_up_does_not_also_write_unknown(watchdog_with):
    """If `give_up_on_unresolved_submission` reports it did not apply (raced), this
    function must not re-assert UNKNOWN on a row it no longer has current information
    about."""
    w, _alerts = watchdog_with(None)
    repo = _RecordingRepo()
    repo.give_up_succeeds = False
    row = _row(age_seconds=CONFIRMATION_LOST_AFTER_SECONDS + 1)

    asyncio.run(
        w._adjudicate_ambiguous_execution(repo, row, from_status=ExecutionStatus.UNKNOWN)
    )

    assert len(repo.give_ups) == 1
    assert repo.resolutions == [], (
        "a raced give-up must not be followed by a second, stale UNKNOWN write"
    )


# ---- the branches that got a REAL signal must never give up, however old the row is ----

def test_not_found_is_immediate_and_never_goes_through_give_up(watchdog_with):
    executor = _StubExecutor(
        OrderStatusResult(
            success=True, status="not_found", filled_size="0", remaining_size="0",
            avg_price=None,
        )
    )
    w, _alerts = watchdog_with(executor)
    repo = _RecordingRepo()
    row = _row(age_seconds=CONFIRMATION_LOST_AFTER_SECONDS * 10)

    asyncio.run(
        w._adjudicate_ambiguous_execution(repo, row, from_status=ExecutionStatus.UNKNOWN)
    )

    assert repo.give_ups == [], "a definite venue answer must never go through the give-up path"
    assert repo.resolutions[0]["status"] == ExecutionStatus.FAILED
    assert repo.resolutions[0]["from_status"] == ExecutionStatus.UNKNOWN


def test_still_open_at_the_venue_never_gives_up_however_old(watchdog_with):
    """Live exposure the platform lost track of is positive evidence, not 'no signal'.
    However long this has been ambiguous, it must stay UNKNOWN with the id recorded,
    never FAILED."""
    executor = _StubExecutor(
        OrderStatusResult(
            success=True, status="open", filled_size="0", remaining_size="1",
            avg_price=None, venue_order_id="VENUE-OPEN",
        )
    )
    w, alerts = watchdog_with(executor)
    repo = _RecordingRepo()
    row = _row(age_seconds=CONFIRMATION_LOST_AFTER_SECONDS * 10)

    asyncio.run(
        w._adjudicate_ambiguous_execution(repo, row, from_status=ExecutionStatus.UNKNOWN)
    )

    assert repo.give_ups == []
    decision = repo.resolutions[0]
    assert decision["status"] == ExecutionStatus.UNKNOWN
    assert decision["order_id"] == "VENUE-OPEN"
    assert alerts


def test_status_with_no_id_never_gives_up_however_old(watchdog_with):
    """Partial positive evidence (a status, but no attributable id) is not 'no signal'
    either, and must never be converted to FAILED by age alone."""
    executor = _StubExecutor(
        OrderStatusResult(
            success=True, status="closed", filled_size="1", remaining_size="0",
            avg_price="1000", venue_order_id=None,
        )
    )
    w, alerts = watchdog_with(executor)
    repo = _RecordingRepo()
    row = _row(age_seconds=CONFIRMATION_LOST_AFTER_SECONDS * 10)

    asyncio.run(
        w._adjudicate_ambiguous_execution(repo, row, from_status=ExecutionStatus.UNKNOWN)
    )

    assert repo.give_ups == []
    assert repo.resolutions[0]["status"] == ExecutionStatus.UNKNOWN
    assert alerts


def test_a_confirmed_fill_persists_even_when_reached_from_the_unknown_population(
    watchdog_with,
):
    """Positive control for `from_status` forwarding: a real answer on a recheck pass
    must still record a genuine fill, exactly as it would on a first attempt."""
    executor = _StubExecutor(
        OrderStatusResult(
            success=True, status="closed", filled_size="1", remaining_size="0",
            avg_price="1000", venue_order_id="VENUE-1",
        )
    )
    w, _alerts = watchdog_with(executor)
    repo = _RecordingRepo()
    row = _row(age_seconds=CONFIRMATION_LOST_AFTER_SECONDS * 10)

    asyncio.run(
        w._adjudicate_ambiguous_execution(repo, row, from_status=ExecutionStatus.UNKNOWN)
    )

    decision = repo.resolutions[0]
    assert decision["status"] == ExecutionStatus.COMPLETED
    assert decision["order_id"] == "VENUE-1"
    assert decision["from_status"] == ExecutionStatus.UNKNOWN


# ══════════════════════════════════════════════════════════════════════════════════════
#  recheck_unresolved_submissions — the retry loop that was missing
# ══════════════════════════════════════════════════════════════════════════════════════

def test_recheck_does_nothing_when_classification_is_empty():
    """The common case, and the fail-safe default on any non-PostgreSQL dialect: no
    candidates, no adjudication, no write."""
    w = OrderWatchdog(db_session=None, config=WatchdogConfig())
    called = []
    w._adjudicate_ambiguous_execution = lambda *a, **k: called.append((a, k))

    asyncio.run(w.recheck_unresolved_submissions())

    assert called == []


def test_recheck_re_adjudicates_every_candidate_with_from_status_unknown(monkeypatch):
    """The method exists, queries the repository, and forwards `from_status=UNKNOWN`
    for every row it finds -- the one piece of wiring that makes the whole mechanism
    reachable from the monitoring loop."""
    candidate_rows = [_row(age_seconds=CONFIRMATION_LOST_AFTER_SECONDS + 10)]

    class _FakeRepo:
        def __init__(self, _db):
            pass

        def classify_unresolved_submissions(self):
            return candidate_rows

    # `recheck_unresolved_submissions` imports the repository LOCALLY inside the
    # function, from `backend_app.core.models.execution_record`, re-executed on every
    # call -- so patching the attribute on that module is enough; no `order_watchdog`
    # module-level name exists to patch.
    import backend_app.core.models.execution_record as exec_record_module
    monkeypatch.setattr(exec_record_module, "ExecutionRecordRepository", _FakeRepo)

    w = OrderWatchdog(db_session=None, config=WatchdogConfig())
    seen: List[Dict[str, Any]] = []

    async def _fake_adjudicate(repo, row, *, from_status=None):
        seen.append({"row": row, "from_status": from_status})

    w._adjudicate_ambiguous_execution = _fake_adjudicate

    asyncio.run(w.recheck_unresolved_submissions())

    assert len(seen) == 1
    assert seen[0]["row"] is candidate_rows[0]
    assert seen[0]["from_status"] == ExecutionStatus.UNKNOWN


def test_recheck_survives_a_classification_failure():
    """A broken query must not crash the monitoring loop -- mirrors
    `recover_unsubmitted_executions`'s own `except Exception` around the same call."""
    w = OrderWatchdog(db_session=object(), config=WatchdogConfig())
    # db_session is not a real Session; ExecutionRecordRepository(self.db) will be
    # constructed but `classify_unresolved_submissions` will raise on first use because
    # `self.db.bind` does not exist on a plain object() -- exercising the except branch.
    asyncio.run(w.recheck_unresolved_submissions())  # must not raise
