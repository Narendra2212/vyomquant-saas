"""DUP-02 — an execution whose submission MAY have reached the venue is never
automatically resubmitted.

THE DEFECT (established in staging/PHASE11B_D2_DUP01_INDEPENDENT_REVIEW.md)
---------------------------------------------------------------------------
    check_idempotent_execution
        CASE 3  FAILED  + allow_failed_retry  -> resets the row to PENDING,
                                                 returns "allow_retry"
        CASE 4  PENDING                       -> returns "allow_retry"

    execute_with_idempotency
        branches on skip_return_result | skip_already_running | skip_failed_no_retry
        *** no branch for "allow_retry" ***   -> falls through to the claim
        -> claim succeeds (the row is PENDING again)
        -> _execute_trade_internal -> place_order -> create_order  ** SECOND SUBMISSION **

and `execution_engine` passes `allow_failed_retry=True`.

Neither branch consulted `submitted_at` — the field TRADE-07 added precisely to record
"this execution was sent to a venue". `mark_submitted` writes it immediately before the
venue call, in its **own committed transaction**, so it survives process death; its
docstring states the consequence of ignoring it outright: *"retrying it risks a second
real order"*.

THE POLICY THESE TESTS ENCODE
----------------------------
Retry is permitted only when the code can establish that **no prior venue submission
could have occurred**. `submitted_at IS NULL` is that establishment, and it is the same
evidence `classify_stale_executions` already uses to separate
`failed_before_submission` from `ambiguous_submission`. This reuses tested semantics
rather than inventing a new notion of ambiguity.

`UNKNOWN` refuses unconditionally: it is the status `OrderWatchdog` assigns when it
**could not determine** whether the order exists at the venue. "We could not tell" can
never license another submission.

SCOPE. Offline. No venue, no credential, no order, no network. Real `ExecutionRecordModel`
rows on in-memory SQLite, so status and `submitted_at` semantics are the product's own
rather than a mock's. **Nothing here establishes venue-side duplicate rejection** — that
needs sandbox scenario 8 and remains unverified on every venue.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend_app.core.models.execution_record import (ExecutionRecordModel,
                                                      ExecutionRecordRepository,
                                                      ExecutionSide, ExecutionStatus)

TENANT = uuid.uuid4()
STRATEGY = "dup02-strategy"
SYMBOL = "BTCUSDT"
#: Frozen so every call derives the SAME execution_id — the condition under which a
#: second tick collides with the first record. A moving timestamp would silently give
#: each invocation its own id and the test would prove nothing.
WHEN = datetime(2026, 10, 10, 12, 0, 0)
QTY = 0.01
PRICE = 50000.0


@pytest.fixture
def repo():
    """A repository over a real in-memory SQLite table."""
    engine = create_engine("sqlite://")
    ExecutionRecordModel.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield ExecutionRecordRepository(session)
    finally:
        session.close()


def _check(repo_, **over):
    """`check_idempotent_execution` with the fixed identity-bearing arguments."""
    kwargs = dict(
        tenant_id=TENANT, strategy_id=STRATEGY, symbol=SYMBOL, timestamp=WHEN,
        side=ExecutionSide.BUY, qty=QTY, price=PRICE,
    )
    kwargs.update(over)
    return repo_.check_idempotent_execution(**kwargs)


def _seed(repo_, status, *, submitted_at=None, order_id=None):
    """Insert the row `check_idempotent_execution` will find, with a chosen history."""
    execution_id, _, _ = _check(repo_)          # derive + insert PENDING
    row = repo_.get_by_id(execution_id, TENANT)
    assert row is not None, "the first check did not create a record"
    row.status = status
    row.submitted_at = submitted_at
    row.order_id = order_id
    repo_.db.commit()
    return execution_id


# ══════════════════════════════════════════════════════════════════════════════════════
#  CASE 1 — FAILED with prior submission evidence must NOT be retried
# ══════════════════════════════════════════════════════════════════════════════════════

def test_failed_after_a_recorded_submission_refuses_the_retry(repo):
    """The core defect. A timeout is recorded FAILED, yet the order may be live.

    `submitted_at` set with `order_id` NULL is exactly `classify_stale_executions`'
    `ambiguous_submission`. Automatically retrying it is a second real order on any venue
    that does not enforce our client order id — which is coinbase and kraken today.
    """
    _seed(repo, ExecutionStatus.FAILED, submitted_at=WHEN)

    _eid, action, _result = _check(repo)

    assert action != "allow_retry", (
        "DUP-01 REPRODUCED: an execution that was already SENT to a venue was cleared "
        "for automatic resubmission. On a venue that does not honour our clientOrderId "
        "this places a second real order."
    )
    assert action == "skip_ambiguous_submission"


def test_the_refused_row_is_not_reset_to_pending(repo):
    """The old code reset FAILED -> PENDING, which is what let the claim succeed again.

    If the status is reset, the next `claim_execution` finds a claimable row and the
    guard is defeated one layer down. Refusing must leave the row alone.
    """
    execution_id = _seed(repo, ExecutionStatus.FAILED, submitted_at=WHEN)

    _check(repo)

    row = repo.get_by_id(execution_id, TENANT)
    assert row.status == ExecutionStatus.FAILED, (
        f"the refused row was transitioned to {row.status}; a claimable status "
        f"re-opens the duplicate-submission path"
    )
    assert row.submitted_at is not None, "submission evidence was erased"


# ══════════════════════════════════════════════════════════════════════════════════════
#  CASE 2 — UNKNOWN must NOT be retried
# ══════════════════════════════════════════════════════════════════════════════════════

def test_unknown_refuses_the_retry(repo):
    """`UNKNOWN` is what the watchdog writes when it COULD NOT establish the outcome.

    It was not among the statuses `check_idempotent_execution` handled at all, so its
    disposition was undefined. "We could not tell" must never license a resubmission.
    """
    _seed(repo, ExecutionStatus.UNKNOWN, submitted_at=WHEN)

    _eid, action, _ = _check(repo)
    assert action == "skip_ambiguous_submission"


def test_unknown_refuses_even_without_submission_evidence(repo):
    """UNKNOWN refuses unconditionally.

    A row can reach UNKNOWN because the venue was unreachable, and that says nothing
    about whether an order exists. Requiring `submitted_at` here would make the refusal
    depend on evidence the status itself already declares unreliable.
    """
    _seed(repo, ExecutionStatus.UNKNOWN, submitted_at=None)

    _eid, action, _ = _check(repo)
    assert action == "skip_ambiguous_submission"


# ══════════════════════════════════════════════════════════════════════════════════════
#  CASE 3 / 6 — a confirmed PRE-submission failure still follows the retry policy
# ══════════════════════════════════════════════════════════════════════════════════════

def test_failed_before_any_submission_is_still_retryable(repo):
    """The guard must not disable legitimate retries — that would be a trading outage.

    `submitted_at IS NULL` is positive evidence that `create_order` was never reached:
    `mark_submitted` commits in its own transaction immediately before the venue call,
    so its absence means the call did not happen.
    """
    _seed(repo, ExecutionStatus.FAILED, submitted_at=None)

    _eid, action, _ = _check(repo)
    assert action == "allow_retry", (
        "a pre-submission failure was refused; the guard is over-broad and blocks "
        "legitimate first submissions"
    )


def test_failed_before_submission_still_honours_retry_disabled(repo):
    """`allow_failed_retry=False` must still win. The guard adds a refusal, not a grant."""
    _seed(repo, ExecutionStatus.FAILED, submitted_at=None)

    _eid, action, _ = _check(repo, allow_failed_retry=False)
    assert action == "skip_failed_no_retry"


def test_ambiguity_wins_over_retry_enabled(repo):
    """`allow_failed_retry=True` must not override submission evidence."""
    _seed(repo, ExecutionStatus.FAILED, submitted_at=WHEN)

    _eid, action, _ = _check(repo, allow_failed_retry=True)
    assert action == "skip_ambiguous_submission"


# ══════════════════════════════════════════════════════════════════════════════════════
#  CASE 3b — PENDING
# ══════════════════════════════════════════════════════════════════════════════════════

def test_pending_without_submission_evidence_follows_first_submission_behaviour(repo):
    """A stale PENDING row that never reached a venue is still retryable."""
    _seed(repo, ExecutionStatus.PENDING, submitted_at=None)

    _eid, action, _ = _check(repo)
    assert action == "allow_retry"


def test_pending_with_submission_evidence_refuses(repo):
    """CASE 4 had the same fall-through, and the same exposure.

    A row left PENDING after `mark_submitted` committed is ambiguous for exactly the
    reason a FAILED one is.
    """
    _seed(repo, ExecutionStatus.PENDING, submitted_at=WHEN)

    _eid, action, _ = _check(repo)
    assert action == "skip_ambiguous_submission"


# ══════════════════════════════════════════════════════════════════════════════════════
#  CASE 4 — EXECUTING keeps its existing concurrent-claim protection
# ══════════════════════════════════════════════════════════════════════════════════════

def test_executing_still_skips_as_already_running(repo):
    """Unchanged behaviour. The guard must not disturb the concurrency protection."""
    _seed(repo, ExecutionStatus.EXECUTING, submitted_at=WHEN)

    _eid, action, _ = _check(repo)
    assert action == "skip_already_running"


def test_completed_still_returns_the_cached_result(repo):
    _seed(repo, ExecutionStatus.COMPLETED, submitted_at=WHEN, order_id="VENUE-1")

    _eid, action, _ = _check(repo)
    assert action == "skip_return_result"


# ══════════════════════════════════════════════════════════════════════════════════════
#  CASE 7 — a repeated tick cannot bypass the guard
# ══════════════════════════════════════════════════════════════════════════════════════

def test_repeated_ticks_never_clear_an_ambiguous_execution(repo):
    """Ten successive ticks, as `BotRunner._inner_loop` would produce.

    The identity-bearing arguments are frozen, so every tick derives the same
    `execution_id` and collides with the same ambiguous row. Not one may be cleared.
    """
    _seed(repo, ExecutionStatus.FAILED, submitted_at=WHEN)

    actions = [_check(repo)[1] for _ in range(10)]

    assert set(actions) == {"skip_ambiguous_submission"}, (
        f"a repeated tick cleared an ambiguous execution: {actions}"
    )


def test_the_execution_id_is_stable_across_ticks(repo):
    """Guards the premise of the test above.

    If the derived id varied per tick, each tick would create a NEW record, the guard
    would never be consulted, and these tests would pass while proving nothing.
    """
    ids = {_check(repo)[0] for _ in range(5)}
    assert len(ids) == 1, f"execution_id is not stable across ticks: {ids}"


# ══════════════════════════════════════════════════════════════════════════════════════
#  CASE 8 — a concurrent worker cannot convert ambiguity into a submission
# ══════════════════════════════════════════════════════════════════════════════════════

def test_a_second_worker_also_refuses_and_cannot_claim(repo):
    """Two repositories over the same database, as two pods would be.

    Both must refuse, and — the decisive part — `claim_execution` must not succeed
    afterwards. Refusing in the check while leaving the row claimable would move the
    defect one layer down rather than fixing it.
    """
    execution_id = _seed(repo, ExecutionStatus.FAILED, submitted_at=WHEN)

    second = ExecutionRecordRepository(
        sessionmaker(bind=repo.db.bind)()
    )
    try:
        assert _check(repo)[1] == "skip_ambiguous_submission"
        assert _check(second)[1] == "skip_ambiguous_submission"

        claimed_a, _ = repo.claim_execution(execution_id, TENANT)
        claimed_b, _ = second.claim_execution(execution_id, TENANT)
        assert not claimed_a and not claimed_b, (
            "an ambiguous execution remained claimable, so the guard can be bypassed "
            "by proceeding straight to the claim"
        )
    finally:
        second.db.close()


# ══════════════════════════════════════════════════════════════════════════════════════
#  CASE 5 — a timeout is not treated as proof of rejection
# ══════════════════════════════════════════════════════════════════════════════════════

def test_a_timeout_shaped_failure_is_not_treated_as_a_rejection(repo):
    """The shape `place_order` produces for a lost response.

    `_handle_ccxt_error` classifies a timeout as `NetworkError` and `place_order` returns
    `status="rejected"`, after which the engine records FAILED — an ambiguous outcome
    recorded as a determinate one. Whatever the recorded status, the presence of
    `submitted_at` means the order may exist, and that must govern.
    """
    execution_id = _seed(repo, ExecutionStatus.FAILED, submitted_at=WHEN)
    row = repo.get_by_id(execution_id, TENANT)
    row.result = {"error": "NetworkError: request timed out", "status": "rejected"}
    repo.db.commit()

    _eid, action, _ = _check(repo)
    assert action == "skip_ambiguous_submission", (
        "a timeout recorded as 'rejected' was taken as proof the venue refused the "
        "order; it is not"
    )


# ══════════════════════════════════════════════════════════════════════════════════════
#  CASE 9 — missing fields and database errors fail safely
# ══════════════════════════════════════════════════════════════════════════════════════

def test_an_unreadable_status_is_refused_by_the_guard():
    """A record whose status cannot be read must not be assumed retryable.

    Asserted against `submission_may_have_reached_venue` directly rather than through a
    persisted row, because the schema will not store one: `execution_records.status` is
    `NOT NULL`, so SQLite (and PostgreSQL) reject the insert. That constraint is itself a
    safety property — the state is unreachable through the database — but the guard is
    still written to refuse it, since it also receives objects from the insert-conflict
    path and from callers this function does not control.
    """
    from backend_app.core.models.execution_record import \
        submission_may_have_reached_venue

    class _NoStatus:
        status = None
        submitted_at = None
        order_id = None

    assert submission_may_have_reached_venue(_NoStatus()) is True
    assert submission_may_have_reached_venue(None) is True


def test_the_guard_clears_only_a_provable_pre_submission_failure():
    """The positive control: the guard must not refuse everything.

    A FAILED row with no submission evidence is the one case it should clear, and the
    only reason the fix does not block legitimate retries.
    """
    from backend_app.core.models.execution_record import \
        submission_may_have_reached_venue

    class _PreSubmission:
        status = ExecutionStatus.FAILED
        submitted_at = None
        order_id = None

    assert submission_may_have_reached_venue(_PreSubmission()) is False


def test_the_guard_refuses_unknown_regardless_of_evidence():
    from backend_app.core.models.execution_record import \
        submission_may_have_reached_venue

    class _Unknown:
        status = ExecutionStatus.UNKNOWN
        submitted_at = None
        order_id = None

    assert submission_may_have_reached_venue(_Unknown()) is True


def test_the_guard_refuses_a_record_that_raises_on_attribute_access():
    """A broken record object must not produce a retry authorisation."""
    from backend_app.core.models.execution_record import \
        submission_may_have_reached_venue

    class _Hostile:
        @property
        def status(self):
            raise RuntimeError("detached from session")

    assert submission_may_have_reached_venue(_Hostile()) is True


def test_an_unreadable_record_does_not_silently_permit_a_retry(repo):
    """If the existence check itself fails, the outcome must not be `allow_retry`.

    Fails closed on the lookup rather than defaulting to permission.
    """
    _seed(repo, ExecutionStatus.FAILED, submitted_at=WHEN)

    def _boom(*_a, **_k):
        raise RuntimeError("database unavailable")

    original = repo.get_by_id
    repo.get_by_id = _boom
    try:
        try:
            _eid, action, _ = _check(repo)
        except Exception:
            return  # raising is an acceptable fail-closed outcome
        assert action != "allow_retry", (
            "a database error produced a retry authorisation"
        )
    finally:
        repo.get_by_id = original


# ══════════════════════════════════════════════════════════════════════════════════════
#  THE ENGINE SIDE — no action may reach the venue by fall-through
# ══════════════════════════════════════════════════════════════════════════════════════

def test_every_action_the_check_can_return_is_handled_explicitly():
    """The structural half of the defect: `allow_retry` had no branch.

    It reached `claim_execution` by fall-through, which is how an ambiguous row became a
    second submission. Any action without an explicit branch is the same bug again, so
    this asserts the two sets agree rather than listing today's names.
    """
    import inspect
    import re

    from backend_app.core.models import execution_record as rec_mod
    from backend_app.core import execution_engine as eng_mod

    check_src = inspect.getsource(
        rec_mod.ExecutionRecordRepository.check_idempotent_execution
    )
    returned = set(re.findall(
        r'"(skip_[a-z_]+|allow_retry|execute)"', check_src
    ))

    engine_src = inspect.getsource(eng_mod.UnifiedExecutionEngine.execute_with_idempotency)
    handled = set(re.findall(r"action\s*==\s*'([a-z_]+)'", engine_src))
    handled |= set(re.findall(r'action\s*==\s*"([a-z_]+)"', engine_src))

    # `execute` is the "no existing record" path and proceeds by design.
    must_be_handled = returned - {"execute"}
    missing = must_be_handled - handled

    assert not missing, (
        f"actions returned by check_idempotent_execution with no explicit branch in "
        f"execute_with_idempotency: {sorted(missing)}. These reach the execution claim "
        f"by fall-through, which is the DUP-01 defect."
    )


def test_the_refusal_action_is_not_accidentally_falsy():
    """The action is compared by value; an empty or None action would fall through."""
    from backend_app.core.models.execution_record import \
        AMBIGUOUS_SUBMISSION_ACTION

    assert AMBIGUOUS_SUBMISSION_ACTION == "skip_ambiguous_submission"
    assert bool(AMBIGUOUS_SUBMISSION_ACTION)
