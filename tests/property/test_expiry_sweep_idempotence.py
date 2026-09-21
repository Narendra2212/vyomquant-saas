"""
tests/property/test_expiry_sweep_idempotence.py - property P-13, the expiry sweep's idempotence.

Spec: marketplace-subscriptions-paper-trading task 20.2. ``design.md § Property-to-test
mapping`` -> ``P-13 | tests/property/test_expiry_sweep_idempotence.py | subscription sets ×
repetition counts | the single-run state set``. Requirement 11.8.

Module under test: ``backend_app/backend/marketplace/expiry_sweep.py`` (task 20.1), hosted by
``backend_app/workers/marketplace_expiry_worker.py``.

THE PROPERTY, AND THE THREE THINGS "SAME STATES" IS NOT ENOUGH TO SAY
--------------------------------------------------------------------
P-13 as written: for all generated sets of Subscriptions and all repetition counts ``n >= 1``,
running the sweep ``n`` times produces the same set of Subscription_States as running it once.

Asserted literally, that claim is satisfied by a sweep that re-expires an already-``expired``
row on every pass: the *state* is idempotent because ``expired -> expired`` is a no-op label
write. What such a sweep would not be is idempotent in its **records**. So this property
asserts, over the same generated population and the same repetition count:

1. **the state set** - ``{id: status}`` after ``n`` passes equals ``{id: status}`` after one;
2. **the history** - passes 2..n write **no** additional ``library_subscription_transitions``
   row, and **no** additional ``SUBSCRIPTION_EXPIRED`` audit entry. A sweep that re-audited
   every 30 seconds would flood the Audit_Log with a transition that happened once, and
   Requirement 11.12's trail would stop being readable as a history of *changes*;
3. **the statements** - each pass after the first costs exactly one statement, the candidate
   read that finds nothing. No UPDATE on ``library_subscriptions``, no insert, and no
   Requirement 11.15 enforcement write. ``stop_running_sessions_and_deployments`` is called for
   newly-expired rows **only**, and is not called again on a repeat pass - which is what stops
   the sweep from re-stamping ``stopped_at`` on a deployment it already stopped;

and, per pass, the predicate's own boundary:

4. a Subscription whose ``period_expiry`` is **exactly** ``now`` IS swept (the bound is
   ``period_expiry <= now``, matching ``entitlement_resolver``'s ``now >= period_expiry`` - the
   two comparisons must agree or P-11 and P-13 would contradict each other), and one strictly in
   the future is NOT, on every pass;
5. only ``active`` and ``suspended`` rows are candidates. ``cancelled``, ``refunded``,
   ``pending``, ``payment_failed`` and already-``expired`` rows keep the exact status they
   started with, however many times the sweep runs;
6. ``marketplace.expiry_sweep.errors`` reads zero for a clean run of ``n`` passes, and exactly
   one after a pass whose candidate read fails - the counter that a defect fixed in 20.1 used to
   leave at zero for the worst failure mode of all (a read that never completes, so nothing is
   ever expired). Asserted rather than trusted, so the fix cannot silently regress.

WHERE THE EXPIRABLE STATUS SET COMES FROM
-----------------------------------------
Not transcribed as ``{'active', 'suspended'}``, and not read off the module under test either -
comparing ``expiry_sweep.EXPIRABLE_STATUS_TEXTS`` against itself would pass for any predicate.
The oracle is the **database's effective permitted transition set**, read through
``tests/test_submission_state_agreement.subscription_permitted_pairs_in_db()`` (008's twelve
seeded pairs ∪ 012's ``('payment_failed','active')`` addendum, both parsed off disk): the states
this sweep may expire from are exactly those with a seeded edge to ``EXPIRED``. So the property
compares the sweep's behaviour against what ``trg_subscription_transition_guard`` would admit,
and a widened predicate fails here before it reaches a database that would refuse it with 23514.

WHY IT DRIVES ``sweep`` WITH AN INJECTED ``now``
------------------------------------------------
``sweep(supabase=..., now=...)`` takes both the handle and the instant, which is the layering
rule ``design.md`` states and ``entitlement_resolver``, ``eligibility_gate`` and
``checkout_service`` already follow. The boundary case of claim 4 is only expressible to the
microsecond with an injected instant.

``MarketplaceExpiryWorker.process_iteration`` deliberately has **no** injectable clock (a
production parameter existing purely for a test was rejected), so the worker leg of this
property anchors the *population* to ``datetime.now(timezone.utc)`` instead - the technique
``_mixed_population(anchor)`` established in ``tests/test_expiry_sweep.py`` - and repeats
``process_iteration`` under a wall clock that moves between passes. That leg is what shows the
idempotence is not an artefact of handing the same frozen instant to every pass.

WHAT THIS PROPERTY DOES NOT CLAIM
---------------------------------
Nothing about entitlement. The sweep is not the authority on it: ``entitlement_resolver.resolve``
compares ``now`` with ``period_expiry`` on every call, whether or not the sweep has ever run
(Requirement 11.7). That is property P-11's subject, asserted through the resolver in
``tests/property/test_entitlement_expiry_boundary.py``; asserting it here through the sweep would
be asserting it through the wrong component.

WHY IT IMPORTS THE DOUBLE FROM ``tests/test_expiry_sweep.py``
-------------------------------------------------------------
``FakeSupabase`` there *applies* ``eq``/``lte``/``in``/``not.is`` rather than merely recording
them, which is the only reason assertions about **which** rows moved mean anything. Task 20.1
owns it. A second copy in this file would be a second thing to keep honest, and the day the two
drifted, one of them would be quietly passing against a filter the other rejects. ``_subscription``
(the row builder), ``_RecordingAuditWriter`` and ``_run_coroutine`` come from the same module for
the same reason - and ``_run_coroutine`` in particular must not be replaced by ``asyncio.run``,
which closes its loop and leaves the thread with none, breaking sibling suites that still call
``get_event_loop().run_until_complete(...)``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from backend_app.backend.marketplace import expiry_sweep as sweep_module
from backend_app.backend.marketplace.expiry_sweep import (
    AUDIT_REASON_CODE,
    EXPIRABLE_STATUS_TEXTS,
    EXPIRED_STATUS_TEXT,
    METRIC_ERRORS,
    METRIC_TRANSITIONS,
    TRANSITION_CAUSE,
    ExpirySweepFailed,
    SweepOutcome,
    sweep,
)
from backend_app.backend.marketplace.subscription_state import (
    SUBSCRIPTION_STATUS_VALUES,
)
from tests.strategies.marketplace_generators import utc_instants
from tests.test_expiry_sweep import (
    FakeSupabase,
    _RecordingAuditWriter,
    _run_coroutine,
    _subscription,
)
from tests.test_submission_state_agreement import subscription_permitted_pairs_in_db

#: The configuration ``design.md § Property-based testing configuration`` prescribes, bounded so
#: the whole file completes in seconds: each example runs up to four sweeps plus one deliberately
#: failing pass against an in-memory double, so 100 examples is ~500 sweeps - the whole file
#: completes in seconds.
#:
#: ``deadline=None``, the convention every sibling in ``tests/property`` follows and the reason
#: ``tests/property/test_settlement_ledger.py`` states: each example runs several ``async``
#: entry points on freshly created event loops, and loop setup/teardown jitter on a loaded
#: machine is easily an order of magnitude wider than the work being timed. A per-example
#: millisecond budget would measure the scheduler, not the sweep, and would flake. The real
#: bound on this file is ``max_examples``, and the real hang detector is the suite timeout.
#: ``derandomize`` is left at its default ``False`` so the ``.hypothesis`` database keeps
#: accumulating failing examples across runs.
PROPERTY_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


# ══════════════════════════════════════════════════════════════════════════
# THE ORACLE: which states the DATABASE permits an edge to EXPIRED from
# ══════════════════════════════════════════════════════════════════════════


def _expirable_status_texts_in_db() -> frozenset:
    """The ``library_subscriptions.status`` spellings a seeded edge to ``EXPIRED`` exists from.

    Derived from the effective permitted pair set the guard reads -- 008's twelve pairs ∪ 012's
    addendum, parsed off disk -- and lower-cased to the column's own casing. Never transcribed,
    and never read off the module under test.
    """
    target = EXPIRED_STATUS_TEXT.upper()
    pairs = subscription_permitted_pairs_in_db()
    assert pairs, "the permitted transition set parsed to nothing; the oracle would be vacuous"
    return frozenset(
        from_state.lower() for from_state, to_state in pairs if to_state == target
    )


#: ``{'active', 'suspended'}`` today -- as a fact about the seeded machine, not as a literal.
DB_EXPIRABLE_STATUS_TEXTS: frozenset = _expirable_status_texts_in_db()

#: Two purchasers × two Listings, so Requirement 11.15's ``(purchaser, Listing)`` scoping has
#: more than one pair to get wrong and a stop issued for one pair is visibly not issued for
#: the other.
_PURCHASERS: Tuple[str, ...] = ("purchaser-a", "purchaser-b")
_LISTINGS: Tuple[str, ...] = ("listing-a", "listing-b")

#: Where a generated row's ``period_expiry`` sits relative to the sweep's instant.
#: ``boundary`` is ``period_expiry == now`` to the microsecond -- the case claim 4 is about.
_EXPIRY_KINDS: Tuple[str, ...] = ("past", "boundary", "future", "null")

#: The kinds that satisfy ``period_expiry IS NOT NULL AND period_expiry <= now``.
_ELIGIBLE_KINDS: frozenset = frozenset({"past", "boundary"})


# ══════════════════════════════════════════════════════════════════════════
# THE GENERATOR
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class _Population:
    """One generated set of Subscriptions, its sweep instant, and a repetition count."""

    anchor: datetime
    rows: Tuple[Dict[str, Any], ...]
    #: ``{subscription_id: one of _EXPIRY_KINDS}``.
    kinds: Dict[str, str]
    #: ``{subscription_id: the status it was created with}``.
    statuses: Dict[str, str]
    #: ``{subscription_id: (user_id, library_id)}``.
    pairs: Dict[str, Tuple[str, str]]
    #: ``n >= 1`` -- how many times the sweep is run.
    repetitions: int

    @property
    def expected_expired_ids(self) -> frozenset:
        """The ids Requirement 11.8 says a sweep at :attr:`anchor` must expire.

        Stated from the requirement's own two clauses against the generated facts -- the status
        the row was created with, and where its expiry sits relative to the instant -- with the
        expirable status set taken from the seeded machine. Nothing here consults the sweep.
        """
        return frozenset(
            sub_id
            for sub_id, kind in self.kinds.items()
            if kind in _ELIGIBLE_KINDS
            and self.statuses[sub_id] in DB_EXPIRABLE_STATUS_TEXTS
        )

    @property
    def untouched_ids(self) -> frozenset:
        """Every other id: the rows that must read exactly what they started with, forever."""
        return frozenset(self.kinds) - self.expected_expired_ids

    def deployments(self) -> List[Dict[str, Any]]:
        """One stoppable deployment per ``(purchaser, Listing)`` pair present in the population.

        Present so Requirement 11.15's enforcement has something to match: a repeat pass that
        re-issued the stop would re-stamp ``stopped_at``, and the snapshot comparison would see
        it.
        """
        return [
            {
                "id": f"dep-{user_id}-{library_id}",
                "user_id": user_id,
                "marketplace_listing_id": library_id,
                "status": "running",
            }
            for user_id, library_id in sorted(set(self.pairs.values()))
        ]

    def paper_sessions(self) -> List[Dict[str, Any]]:
        """One ``RUNNING`` Paper_Session per pair, for the persisted half of 11.15."""
        return [
            {
                "id": f"sess-{user_id}-{library_id}",
                "user_id": user_id,
                "listing_id": library_id,
                "session_state": "RUNNING",
            }
            for user_id, library_id in sorted(set(self.pairs.values()))
        ]


@st.composite
def sweep_populations(draw: Any, *, anchor: Optional[datetime] = None) -> _Population:
    """Subscription sets × repetition counts (``design.md``'s generator for P-13).

    Every status in Requirement 11.1's seven-value set is drawable, and every position relative
    to the sweep instant is drawable, so one example can carry an eligible row, a boundary row,
    a future row, a null-period row and an already-``expired`` row at once. The offsets are
    drawn rather than fixed, so "five minutes ago" is not the only past the predicate ever sees.

    Args:
        anchor: fixes the instant the population is built around. The worker leg passes
            ``datetime.now(timezone.utc)`` because ``process_iteration`` reads the wall clock and
            has no injectable clock; the main leg leaves it unset and draws one.
    """
    instant = anchor if anchor is not None else draw(utc_instants(min_year=2001, max_year=2099))
    repetitions = draw(st.integers(min_value=1, max_value=4))
    row_count = draw(st.integers(min_value=1, max_value=6))

    rows: List[Dict[str, Any]] = []
    kinds: Dict[str, str] = {}
    statuses: Dict[str, str] = {}
    pairs: Dict[str, Tuple[str, str]] = {}

    for index in range(row_count):
        status = draw(st.sampled_from(SUBSCRIPTION_STATUS_VALUES))
        kind = draw(st.sampled_from(_EXPIRY_KINDS))
        # One second to sixty days. The one-second floor keeps ``future`` STRICTLY future and
        # ``past`` strictly past, so ``boundary`` is the only kind sitting exactly on ``<=``.
        offset = timedelta(seconds=draw(st.integers(min_value=1, max_value=60 * 86_400)))
        user_id = draw(st.sampled_from(_PURCHASERS))
        library_id = draw(st.sampled_from(_LISTINGS))

        if kind == "past":
            expiry: Optional[datetime] = instant - offset
        elif kind == "boundary":
            expiry = instant
        elif kind == "future":
            expiry = instant + offset
        else:
            expiry = None

        sub_id = f"sub-{index}"
        rows.append(
            _subscription(
                sub_id,
                status,
                period_expiry=expiry,
                user_id=user_id,
                library_id=library_id,
            )
        )
        kinds[sub_id] = kind
        statuses[sub_id] = status
        pairs[sub_id] = (user_id, library_id)

    return _Population(
        anchor=instant,
        rows=tuple(rows),
        kinds=kinds,
        statuses=statuses,
        pairs=pairs,
        repetitions=repetitions,
    )


# ══════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════


def _client_for(population: _Population) -> FakeSupabase:
    """A filter-applying double loaded with the population and something to stop."""
    return FakeSupabase(
        subscriptions=[dict(row) for row in population.rows],
        deployments=population.deployments(),
        paper_sessions=population.paper_sessions(),
    )


def _statement_shapes(client: FakeSupabase, since: int) -> List[Tuple[str, str]]:
    """``(op, table)`` for every statement executed after index ``since``."""
    return [(q.op, q.table_name) for q in client.statements[since:]]


def _expired_audit_calls(writer: _RecordingAuditWriter) -> List[Dict[str, Any]]:
    """The ``SUBSCRIPTION_EXPIRED`` audit entries the writer received, by the act's own name."""
    return [
        call
        for call in writer.calls
        if (call.get("metadata") or {}).get("audited_act") == AUDIT_REASON_CODE
    ]


class _StopSpy:
    """Records every ``stop_running_sessions_and_deployments`` call, then delegates to the real one.

    Patched onto the module because :func:`sweep` resolves the name as a module global at call
    time. The real implementation still runs, so the enforcement writes it makes are the ones the
    snapshot comparison sees -- this spy adds an observation, it does not replace behaviour.
    """

    def __init__(self) -> None:
        self.calls: List[Tuple[str, ...]] = []
        self._original = sweep_module.stop_running_sessions_and_deployments

    async def __call__(self, rows: Any, **kwargs: Any) -> Any:
        self.calls.append(tuple(r.subscription_id for r in rows))
        return await self._original(rows, **kwargs)

    def __enter__(self) -> "_StopSpy":
        sweep_module.stop_running_sessions_and_deployments = self  # type: ignore[assignment]
        return self

    def __exit__(self, *exc: Any) -> None:
        sweep_module.stop_running_sessions_and_deployments = (  # type: ignore[assignment]
            self._original
        )


def _run_sweep(
    client: FakeSupabase, *, now: datetime, writer: _RecordingAuditWriter
) -> SweepOutcome:
    """One pass, on its own event loop (see the module docstring on ``_run_coroutine``)."""
    return _run_coroutine(sweep(supabase=client, now=now, audit_writer=writer))


# ══════════════════════════════════════════════════════════════════════════
# THE PROPERTY
# ══════════════════════════════════════════════════════════════════════════

# Feature: marketplace-subscriptions-paper-trading, Property 13 (idempotence, sweep): For all
# generated sets of Subscriptions and all repetition counts n >= 1: running the expiry sweep n
# times produces the same set of Subscription_States as running it once.
def test_p13_expiry_sweep_is_idempotent() -> None:
    """``n >= 1`` passes leave the same states, the same history and the same audit trail as one.

    **Validates: Requirements 11.8**
    """
    # Non-vacuity counters. A property over generated populations can pass by never generating
    # the interesting row, so the cases that carry the claim are counted and the counts are
    # asserted after the run.
    seen = {
        "examples": 0,
        "repeated": 0,  # n >= 2, so there IS a repeat pass to be a no-op
        "expired_something": 0,  # the first pass had work to do
        "boundary_expired": 0,  # period_expiry == now exactly, and it was swept
        "future_untouched": 0,  # an expirable status with a strictly future expiry, left alone
        "null_untouched": 0,  # an expirable status with no period at all, left alone
        "already_expired": 0,  # an 'expired' row with a past expiry, never re-expired
        "unexpirable_status": 0,  # cancelled/refunded/pending/payment_failed, past expiry
        "stopped_something": 0,  # Requirement 11.15's enforcement actually ran
    }

    @PROPERTY_SETTINGS
    @given(population=sweep_populations())
    def check(population: _Population) -> None:
        # The oracle must be about the machine the database seeds, and the sweep's own derived
        # set must agree with it. Asserted here so a widened predicate fails as this property
        # rather than as a 23514 in production.
        assert set(EXPIRABLE_STATUS_TEXTS) == set(DB_EXPIRABLE_STATUS_TEXTS), (
            "the sweep expires from "
            f"{sorted(EXPIRABLE_STATUS_TEXTS)} but the seeded machine permits an edge to "
            f"{EXPIRED_STATUS_TEXT!r} only from {sorted(DB_EXPIRABLE_STATUS_TEXTS)}"
        )

        sweep_module.reset_metrics()
        anchor = population.anchor
        expected = population.expected_expired_ids
        untouched = population.untouched_ids

        client = _client_for(population)
        writer = _RecordingAuditWriter()
        before = client.statuses()

        seen["examples"] += 1
        if population.repetitions >= 2:
            seen["repeated"] += 1

        with _StopSpy() as stop_spy:
            # ── Pass 1: the reference. Everything the sweep will ever do, it does here. ──
            first = _run_sweep(client, now=anchor, writer=writer)

            assert first.ok, f"the first pass failed: {first.failures}"
            assert {r.subscription_id for r in first.expired} == expected, (
                f"at now={anchor.isoformat()} the sweep expired "
                f"{sorted(r.subscription_id for r in first.expired)}; Requirement 11.8 says "
                f"{sorted(expected)} (kinds={population.kinds}, statuses={population.statuses})"
            )

            after_first = client.snapshot()
            states_after_first = client.statuses()
            transitions_after_first = client.rows("library_subscription_transitions")
            audits_after_first = list(_expired_audit_calls(writer))
            stop_calls_after_first = list(stop_spy.calls)

            # One history row and one audit entry per expired row, and no more (Req 11.12, 26.2).
            assert first.transitions_written == len(expected)
            assert first.audit_entries_written == len(expected)
            assert len(transitions_after_first) == len(expected)
            assert len(audits_after_first) == len(expected)
            for row in transitions_after_first:
                assert row["from_state"] in DB_EXPIRABLE_STATUS_TEXTS
                assert row["to_state"] == EXPIRED_STATUS_TEXT
                assert row["cause"] == TRANSITION_CAUSE

            # Claim 4/5, pass 1: the boundary row moved, and nothing else did.
            for sub_id in expected:
                assert states_after_first[sub_id] == EXPIRED_STATUS_TEXT
                if population.kinds[sub_id] == "boundary":
                    seen["boundary_expired"] += 1
            for sub_id in untouched:
                assert states_after_first[sub_id] == before[sub_id], (
                    f"{sub_id} (status={population.statuses[sub_id]}, "
                    f"kind={population.kinds[sub_id]}) must not have been touched"
                )
                status, kind = population.statuses[sub_id], population.kinds[sub_id]
                if status in DB_EXPIRABLE_STATUS_TEXTS and kind == "future":
                    seen["future_untouched"] += 1
                if status in DB_EXPIRABLE_STATUS_TEXTS and kind == "null":
                    seen["null_untouched"] += 1
                if status == EXPIRED_STATUS_TEXT and kind in _ELIGIBLE_KINDS:
                    seen["already_expired"] += 1
                if status not in DB_EXPIRABLE_STATUS_TEXTS and kind in _ELIGIBLE_KINDS:
                    seen["unexpirable_status"] += 1

            # Requirement 11.15's enforcement ran for the newly-expired rows, and only for them.
            if expected:
                seen["expired_something"] += 1
                assert stop_calls_after_first == [
                    tuple(r.subscription_id for r in first.expired)
                ], f"the stop was issued as {stop_calls_after_first}"
                if first.deployments_stopped or first.paper_sessions_stopped:
                    seen["stopped_something"] += 1
            else:
                assert stop_calls_after_first == [], (
                    "nothing expired, so no enforcement stop may be issued"
                )

            # ── Passes 2..n: no state change, no record, no statement but the read. ──
            for repetition in range(2, population.repetitions + 1):
                statements_before = len(client.statements)
                outcome = _run_sweep(client, now=anchor, writer=writer)

                assert outcome.ok, f"pass {repetition} failed: {outcome.failures}"
                assert outcome.expired_count == 0, (
                    f"pass {repetition} expired "
                    f"{sorted(r.subscription_id for r in outcome.expired)} again"
                )
                assert outcome.transitions_written == 0
                assert outcome.audit_entries_written == 0
                assert outcome.deployments_stopped == 0
                assert outcome.paper_sessions_stopped == 0

                # Claim 2 - no additional transition row, no additional audit entry.
                assert client.rows("library_subscription_transitions") == (
                    transitions_after_first
                ), f"pass {repetition} wrote an additional transition row"
                assert _expired_audit_calls(writer) == audits_after_first, (
                    f"pass {repetition} wrote an additional {AUDIT_REASON_CODE} audit entry"
                )

                # Claim 3 - one statement, and it is the candidate read that found nothing.
                assert _statement_shapes(client, statements_before) == [
                    ("select", "library_subscriptions")
                ], (
                    f"pass {repetition} issued "
                    f"{_statement_shapes(client, statements_before)}"
                )
                assert stop_spy.calls == stop_calls_after_first, (
                    f"pass {repetition} re-issued the Requirement 11.15 stop"
                )

                # Claims 1, 4 and 5 - every table byte-for-byte what pass 1 left behind, so the
                # boundary row is still expired, the future row is still not, and the
                # unexpirable statuses still read exactly what they were created with.
                assert client.snapshot() == after_first, (
                    f"pass {repetition} changed persisted state"
                )

        # ── The property as literally stated: n passes, one pass, same state set. ──
        assert client.statuses() == states_after_first
        expected_states = {
            sub_id: (
                EXPIRED_STATUS_TEXT if sub_id in expected else population.statuses[sub_id]
            )
            for sub_id in population.kinds
        }
        assert client.statuses() == expected_states

        # Claim 6 - the error and transition counters, over the whole clean run.
        metrics = sweep_module.metrics_snapshot()
        assert metrics[METRIC_ERRORS] == 0, (
            f"{population.repetitions} clean pass(es) reported {metrics[METRIC_ERRORS]} error(s)"
        )
        assert metrics[METRIC_TRANSITIONS] == len(expected)
        assert sweep_module.last_run_at() == anchor

        # ── And one failing pass: it must be COUNTED, and must change nothing. ──
        #
        # A failed candidate read is the worst failure mode the sweep has - nothing is ever
        # expired - and it is the one that used to report zero errors, because
        # ``_errors_total += len(failures)`` sat only in the late path and the raise went past
        # it. The counter is asserted, not assumed.
        errors_before = sweep_module.metrics_snapshot()[METRIC_ERRORS]
        client.raise_on = {("select", "library_subscriptions")}
        with pytest.raises(ExpirySweepFailed) as caught:
            _run_sweep(client, now=anchor, writer=writer)
        client.raise_on = set()

        assert caught.value.stage == "candidate_read"
        assert sweep_module.metrics_snapshot()[METRIC_ERRORS] == errors_before + 1, (
            "a failed candidate read must be counted on marketplace.expiry_sweep.errors"
        )
        assert client.snapshot() == after_first, "the failed pass changed persisted state"
        # ``last_run_at`` does not advance for a failed pass, so the health check goes stale.
        assert sweep_module.last_run_at() == anchor

    try:
        check()
    finally:
        sweep_module.reset_metrics()

    # ── Non-vacuity: every clause of the property was actually exercised. ──
    assert seen["examples"] >= 50, seen
    assert seen["repeated"] >= 10, f"no repeat pass was ever generated: {seen}"
    assert seen["expired_something"] >= 10, f"the sweep never had work to do: {seen}"
    assert seen["boundary_expired"] >= 3, f"period_expiry == now was never swept: {seen}"
    assert seen["future_untouched"] >= 3, f"a strictly future expiry never occurred: {seen}"
    assert seen["null_untouched"] >= 3, f"a null period never occurred: {seen}"
    assert seen["already_expired"] >= 3, f"an already-expired row never occurred: {seen}"
    assert seen["unexpirable_status"] >= 3, f"an unexpirable status never occurred: {seen}"
    assert seen["stopped_something"] >= 3, f"Requirement 11.15 never enforced anything: {seen}"


# ══════════════════════════════════════════════════════════════════════════
# THE SAME PROPERTY UNDER THE WORKER'S WALL CLOCK
# ══════════════════════════════════════════════════════════════════════════


def test_the_hosted_worker_is_idempotent_across_repeated_iterations() -> None:
    """``n`` worker iterations leave what one leaves, with the clock moving between passes.

    Not a second claim on P-13's number -- it is the same property, driven through the component
    that actually hosts it in production. ``MarketplaceExpiryWorker.process_iteration`` passes no
    ``now``, so the sweep reads the wall clock; the population is therefore anchored to
    ``datetime.now(timezone.utc)`` (the technique ``_mixed_population(anchor)`` established),
    which is what makes the future rows still future and the past rows still past when the second
    iteration runs a few milliseconds later.

    That moving clock is the point: it shows the idempotence is a consequence of the sweep's
    predicate excluding the state it writes, and not an artefact of handing every pass the same
    frozen instant.

    The population is ``_mixed_population(anchor)`` -- task 20.1's one-row-per-interesting-case
    set -- rather than a drawn one: a generated population would have to be drawn against a clock
    read at generation time, which makes the example unreplayable. The drawn populations are the
    other test in this file; this one is the wall-clock leg.

    **Validates: Requirements 11.8**
    """
    from backend_app.workers.marketplace_expiry_worker import MarketplaceExpiryWorker

    from tests.test_expiry_sweep import ELIGIBLE_IDS, LISTING, PURCHASER, _mixed_population

    anchor = datetime.now(timezone.utc)
    rows = _mixed_population(anchor)

    # The expected set, stated from Requirement 11.8's two clauses against the rows themselves
    # and the seeded machine -- then cross-checked against 20.1's own reading of the same
    # population, so the two files cannot disagree about which rows are eligible.
    expected = frozenset(
        row["id"]
        for row in rows
        if row["period_expiry"] is not None
        and datetime.fromisoformat(row["period_expiry"]) <= anchor
        and row["status"] in DB_EXPIRABLE_STATUS_TEXTS
    )
    assert expected == ELIGIBLE_IDS, (
        f"this file reads {sorted(expected)} as eligible; tests/test_expiry_sweep.py reads "
        f"{sorted(ELIGIBLE_IDS)}"
    )

    client = FakeSupabase(
        subscriptions=rows,
        deployments=[
            {
                "id": "dep-1",
                "user_id": PURCHASER,
                "marketplace_listing_id": LISTING,
                "status": "running",
            }
        ],
        paper_sessions=[
            {
                "id": "sess-1",
                "user_id": PURCHASER,
                "listing_id": LISTING,
                "session_state": "RUNNING",
            }
        ],
    )
    writer = _RecordingAuditWriter()
    worker = MarketplaceExpiryWorker(supabase=client, worker_name="test-p13-worker")

    original_sweep = sweep_module.sweep

    async def _sweep_with_writer(**kwargs: Any) -> Any:
        kwargs.setdefault("audit_writer", writer)
        return await original_sweep(**kwargs)

    sweep_module.reset_metrics()
    sweep_module.sweep = _sweep_with_writer  # type: ignore[assignment]
    try:
        _run_coroutine(worker.process_iteration())
        assert worker.last_outcome is not None and worker.last_outcome.ok
        first_ids = {r.subscription_id for r in worker.last_outcome.expired}
        assert first_ids == expected

        after_first = client.snapshot()
        audits_after_first = list(_expired_audit_calls(writer))

        for repetition in range(2, 5):
            statements_before = len(client.statements)
            _run_coroutine(worker.process_iteration())

            assert worker.last_outcome is not None
            assert worker.last_outcome.expired_count == 0, (
                f"worker iteration {repetition} expired rows again"
            )
            assert client.snapshot() == after_first
            assert _expired_audit_calls(writer) == audits_after_first
            assert _statement_shapes(client, statements_before) == [
                ("select", "library_subscriptions")
            ]

        assert sweep_module.metrics_snapshot()[METRIC_ERRORS] == 0
    finally:
        sweep_module.sweep = original_sweep  # type: ignore[assignment]
        sweep_module.reset_metrics()
