"""
tests/property/test_order_idempotence_under_retry.py - task 12.1 (production-launch-hardening).

Spec: production-launch-hardening tasks.md task 12.1, bugfix.md clauses 1.11 / 2.11.

WHAT 1.11 CLAIMED, AND WHY THIS FILE EXISTS RATHER THAN A NEW HARNESS
----------------------------------------------------------------------
Clause 1.11 asserts that "no test in this tree establishes" order-submission idempotency under
retry. That is wrong for the LIVE path:
``tests/crash_recovery/test_worker_crash_mid_submission.py`` already proves it there, at the
signal/exchange layer, across roughly twenty tests - including
``test_the_durable_unique_index_refuses_a_second_row_for_the_same_key``,
``test_the_venue_itself_refuses_a_second_order_under_the_same_key`` and
``test_a_resumed_worker_that_submits_anyway_still_creates_no_second_order``. That module is not
touched here: task 12.7 owns its docstring, and duplicating its coverage in a parallel file would
produce two partial answers instead of one complete one, which is exactly the failure mode
tasks.md's header for this wave (the "Four say EXTEND" paragraph) exists to prevent.

The genuine gap 1.11 pointed at is narrower than its own text: a **property test**, on the
**paper** path, over **generated** submit/retry/restart sequences. ``test_paper_idempotence.py``
already has a property for one idempotency key in one session
(``test_p22_duplicate_order_intent_yields_one_order``, Requirements 16.8, 16.11, 16.15), but its
sequences are hand-built - a first intent plus three *forced* variant kinds
(identical / canonical / mismatch). This file EXTENDS that property rather than replacing it: it
draws the **sequence shape itself** (2-10 steps: submit, retry, restart - never a second distinct
first submission) and asserts the two claims task 12.1 names in those words - order count invariant
at exactly one, and every submission after the first answered with **that** order - reusing P-22's
harness, generators and error contract wherever they fit rather than reinventing them.

WHAT IS DRIVEN, AND WHAT IS NOT DOUBLED
----------------------------------------
The real ``paper_simulator.submit_intent`` is driven against
``tests/test_paper_repository.FakeSupabase``, the one Persistence_Layer double this repository
has - imported through ``tests/test_paper_order_lifecycle_writes``'s harness
(``_seed``, ``_config``, ``_intent``, ``_submit``, ``_snapshot``, the module constants), exactly as
``test_paper_idempotence.py`` does. No second double, no mock, no parallel harness.

WHAT "RETRY" AND "RESTART" MEAN HERE, AND WHY THAT IS THE HONEST READING
--------------------------------------------------------------------------
``paper_simulator.submit_intent`` carries no in-process state between calls: every attempt reads
the account and probes the idempotency key fresh, against the Persistence_Layer double, inside the
one attempt (see its own docstring, step 1). There is therefore nothing in this path for a crashed
worker to lose - the durable store is the only thing correctness can depend on, which is precisely
what a paper/simulation worker restart is a claim ABOUT.

    ``submit``   the first submission of the sequence: the intent this file's generator built,
                 issued with a fresh ``SessionConfig``.
    ``retry``    a repeat issued with the SAME ``intent`` mapping and the SAME ``SessionConfig``
                 object the previous step used - the shape a caller's own retry loop takes when
                 it resends its own request unchanged.
    ``restart``  a repeat issued with an INDEPENDENTLY REBUILT ``SessionConfig`` (a fresh call to
                 ``_config()`` with the same values) and a COPY of the intent mapping rather than
                 the same object - the shape a resumed worker process takes: nothing it holds in
                 memory survived, only what is in the store. If correctness depended on some
                 in-process object surviving between calls, a ``restart`` step is exactly the case
                 that would expose it, because the object is never reused across one.

Every step (submit, retry AND restart alike) carries the SAME idempotency key and the SAME order
parameters - this file is not P-22's conflict case, which already lives there. What is new here is
the retry/restart SEQUENCE SHAPE, drawn rather than hand-built, and read directly against task
12.1's own two sentences.

PAPER AND SIMULATION ONLY
--------------------------
No live exchange adapter is constructed or imported anywhere in this module. ``submit_intent`` is
the paper order path; it never reaches a venue. See task 12.2 / ``test_paper_live_separation.py``
for the separate proof that no code path CAN route a paper signal to a live adapter - that proof is
that module's, not this one's, and is not re-derived here.

THE ORACLE, AND WHY IT DOES NOT ASK THE MODULE WHAT IT DID
-------------------------------------------------------------
The oracle is the row the FIRST submission persisted, read directly off the Persistence_Layer
double (``supabase.orders``) rather than off ``SubmitOutcome.duplicate`` or any other self-report.
For every step after the first: the store still holds exactly one ``paper_orders`` row for the
account, that row's ``id`` and ``idempotency_key`` are unchanged from what the first submission
wrote, and the step's own response names that same ``id``. ``SubmitOutcome.duplicate`` is asserted
as a further claim, never as the oracle - exactly P-22's own convention
(see its module docstring, "THE ORACLES" section).

THE CODE READING (task 12.1: "per requirement 2.11")
-------------------------------------------------------
The idempotency key column and its uniqueness constraint, read directly out of the migrations
rather than inferred from the module's behaviour:

* ``backend_app/migrations/009_paper_trading.sql:696-699`` - the column's own comment:
  "idempotency_key + uq_paper_order_idem: Requirement 16.11's dedupe ... enforced by the database
  as well as by the in-memory cache it replaces."
* ``backend_app/migrations/009_paper_trading.sql:738`` -
  ``CONSTRAINT uq_paper_order_idem UNIQUE (session_id, idempotency_key)`` on ``paper_orders``. This
  is the constraint every sequence in this file exercises: every step in a sequence submits under
  one ``SESSION`` (the harness's constant) and one generated key, so a second row for that pair is
  exactly what this index refuses.
* ``backend_app/migrations/009_paper_trading.sql:737-738`` -
  ``CONSTRAINT chk_paper_order_idem_len CHECK (idempotency_key IS NULL OR (length(idempotency_key)
  BETWEEN 1 AND 128))`` - the backstop on the key's own shape, which is why this file's generator
  draws keys within that range.
* ``backend_app/migrations/013_paper_default_account_children.sql:552-561`` -
  ``uq_paper_order_idem_default``, the PARTIAL companion unique index on
  ``(account_id, idempotency_key) WHERE session_id IS NULL AND idempotency_key IS NOT NULL``, added
  because SQL treats two ``NULL`` ``session_id``s as distinct so the base constraint alone would
  not de-duplicate a default-account order. This file drives every sequence through a real
  ``SESSION`` id (via ``_seed``), so ``uq_paper_order_idem`` is the constraint in force for every
  case generated here; the default-account companion is out of this file's scope precisely because
  it needs a NULL-session premise this harness does not build, and is P-22's/the repository suite's
  ground already (``tests/test_paper_repository.py``).

The durable index is also asked DIRECTLY in this file (:func:`_assert_the_durable_index_refuses_a_
second_order_row`), copied from ``test_paper_idempotence.py``'s own function of the same purpose:
``submit_intent``'s own probe is a READ inside the attempt, and a read can be overtaken by a
genuinely concurrent first request, so what makes "one order per key per session" hold under an
interleaving the in-code probe cannot see is the index itself.

NON-VACUITY
-----------
Trivially satisfiable by a generator that always drew ``submit`` alone at length one. The
generator therefore FORCES every sequence to length at least two (task 12.1's own floor), so a
retry or a restart genuinely happens on every example, and draws the remaining slots - their count,
their kind (retry or restart) and their order - and the order flavour (market / limit) freely. A
length-2 sequence is one repeat and cannot itself contain both step kinds, so what is asserted as a
CENSUS rather than forced into every draw is that ``retry`` and ``restart`` are each common enough
over the whole 100-example run, together with both flavours, a length-2 sequence, a sequence at or
above length 5, and the generator's own ceiling of length 10. A
:class:`~tests.property.paper_census.Recorder`, following ``test_paper_idempotence.py``'s and
``test_paper_order_lifecycle.py``'s own convention, guards every one of those floors rather than
assuming them.

GAPS LEFT OPEN, STATED RATHER THAN ASSERTED AROUND
-----------------------------------------------------
1. **Sequential, not concurrent.** Every step in a sequence is awaited to completion before the
   next is issued. A genuinely concurrent pair of first submissions racing the in-code probe is
   ``test_paper_confluence.py``'s subject (P-23); this file's claim is about repetition over time
   (retry after a timeout, or after a process restart), which is what task 12.1's own words name,
   and the direct index probe above is what still holds under an interleaving this file's own
   sequencing cannot produce.
2. **One session, one key at a time.** As in P-22, two sessions reusing one key is
   ``tests/test_paper_repository.py``'s ground, not this file's.
3. **The rejected flavour is not re-driven here.** A statically rejected first submission, and its
   duplicate returning its recorded rejection reason, are P-22's own forced cases
   (``recorded_order_was_REJECTED``); re-forcing them into a retry/restart SEQUENCE property would
   assert nothing about sequencing that P-22 has not already asserted about repetition, so this
   file forces the two flavours that leave a submittable, fillable order instead (market, limit),
   which is what a duplicate real order would look like if this property failed.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, Mapping, Tuple

import pytest
from hypothesis import HealthCheck, Phase, example, given, settings
from hypothesis import strategies as st

from backend_app.backend.paper import paper_repository as repo
from backend_app.backend.paper import paper_simulator as sim
from backend_app.backend.paper.paper_order_state import PaperOrderState

# ── The census, written once for the paper property modules that need it. ─────────────────
from tests.property.paper_census import Recorder, publish_hypothesis_statistics

# ── The shared harness. A second one would be a second account of what a session is. ──────
from tests.test_paper_order_lifecycle_writes import (
    SESSION,
    _config,
    _intent,
    _seed,
    _snapshot,
    _submit,
)

#: ``design.md § Property-based testing configuration``: at least 100 examples, no per-example
#: deadline. One example drives up to ten submissions through the real simulator and repository,
#: so ``too_slow`` is suppressed rather than the example count being cut - exactly
#: ``test_paper_idempotence.py``'s own choice, for the same reason.
EXAMPLES = 100

PROPERTY_SETTINGS = settings(
    max_examples=EXAMPLES,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)

#: The capital every driver starts from. Large enough that no forced case is ever an accidental
#: ``INSUFFICIENT_FUNDS`` - this property is not about the funds check.
RICH = Decimal("1000000")

#: The two flavours forced into every example. Both leave a real, submittable order rather than a
#: persisted rejection - see the module docstring's gap 3 for why the rejected flavour is not
#: re-driven here.
#:
#: ``limit``   accepted and left resting (ACCEPTED)
#: ``market``  accepted and filled on acceptance (FILLED)
ORDER_FLAVOURS: Tuple[str, ...] = ("limit", "market")

#: The three step kinds a sequence may draw. ``submit`` never appears after the first position -
#: that would be a second FIRST submission, which is a different key by construction and out of
#: this property's scope (P-22 already covers "a second, materially different intent" under
#: ``mismatch``).
STEP_KINDS: Tuple[str, ...] = ("retry", "restart")

#: Sequence lengths this file draws (2-10, per task 12.1). The floor is 2 rather than 1 so that
#: EVERY example genuinely repeats the key at least once - at length 1 the property would hold for
#: free, which is exactly the vacuous case the design doc's non-vacuity convention forbids resting
#: on.
MIN_SEQUENCE_LENGTH = 2
MAX_SEQUENCE_LENGTH = 10

SUBMIT_QUANTITIES: Tuple[str, ...] = ("0.25", "1", "2")
SUBMIT_LIMIT_PRICES: Tuple[str, ...] = ("50", "100", "96.25", "120.50")
SUBMIT_CLOSES: Tuple[str, ...] = ("50", "100", "96.25")
SUBMIT_FEE_RATES: Tuple[str, ...] = ("0", "0.001", "0.0025")


class _RetrySequence:
    """One generated first intent, and the submit/retry/restart sequence that follows it."""

    __slots__ = ("key", "flavour", "side", "quantity", "limit_price", "close", "fee_rate", "steps")

    def __init__(
        self,
        key: str,
        flavour: str,
        side: str,
        quantity: str,
        limit_price: str,
        close: str,
        fee_rate: str,
        steps: Tuple[str, ...],
    ) -> None:
        self.key = key
        self.flavour = flavour
        self.side = side
        self.quantity = quantity
        self.limit_price = limit_price
        self.close = close
        self.fee_rate = fee_rate
        self.steps = steps

    def __repr__(self) -> str:  # pragma: no cover - shrinker output
        return (
            f"_RetrySequence(key={self.key!r}, flavour={self.flavour!r}, side={self.side!r}, "
            f"quantity={self.quantity!r}, limit={self.limit_price!r}, close={self.close!r}, "
            f"fee={self.fee_rate!r}, steps={self.steps})"
        )


@st.composite
def submit_retry_restart_sequences(draw: Any) -> _RetrySequence:
    """A first intent, and a 2-10 STEP TOTAL sequence over :data:`STEP_KINDS` that follows it.

    ``length`` (2-10, per task 12.1) counts the FIRST submission plus every repeat, so
    ``len(steps) == length - 1`` ranges 1-9. Each of those slots draws freely from
    :data:`STEP_KINDS`, so a length-2 sequence (one repeat) is a real, reachable case, and which
    kind that lone repeat is - retry or restart - is generated rather than fixed.

    Nothing here forces both step kinds into every INDIVIDUAL example - a length-2 sequence
    structurally cannot contain both. What is forced is that ``retry`` and ``restart`` are EACH
    common enough across the whole run to clear their own census floors (see :data:`P12_1_FLOORS`),
    which is the honest non-vacuity claim for a property whose sequences can be as short as one
    repeat: "both kinds are exercised, over the run" rather than "both kinds appear in lockstep on
    every draw".

    ``key`` is drawn as text rather than fixed, exactly as P-22 draws it, because
    ``uq_paper_order_idem`` arbitrates on the key's value and a single literal would leave the
    property silent about any key but that one.

    DRAW ORDER, AND WHY ``flavour`` IS DRAWN FIRST
    -------------------------------------------------
    Every plain, unfiltered ``st.sampled_from``/``st.integers`` draw is placed before the two
    draws with variable internal consumption - ``steps`` (a variable-length ``st.lists``) and
    ``key`` (a filtered ``st.text``, i.e. internal rejection sampling). A filtered or
    variable-length draw can consume an unpredictable amount of the underlying choice sequence,
    which was observed to bias the very next plain ``sampled_from`` draw: with ``flavour`` drawn
    immediately after the filtered ``key``, empirical sampling put ``market`` at roughly 30% of
    draws instead of the intended 50/50, well outside sampling noise (confirmed reproducible
    across six independent 200-example trials, never once near parity). Moving ``flavour`` to be
    the FIRST draw in the function - ahead of ``length`` and ``steps`` as well as ``key`` - removes
    the dependency on draw order entirely, rather than only swapping it past the one filtered draw
    that was implicated; isolating each candidate cause (the filtered ``key`` draw alone, the
    ``length``/``steps`` draws alone, and the two combined) showed the skew requires both effects
    stacked immediately before ``flavour``, so drawing ``flavour`` first is the fix least dependent
    on which later draw might disturb the byte stream next. ``side``, ``quantity``,
    ``limit_price``, ``close`` and ``fee_rate`` are unfiltered ``sampled_from`` draws with nothing
    filtered ahead of them in the original order either, so they are left in place.
    """
    flavour = draw(st.sampled_from(ORDER_FLAVOURS))
    length = draw(st.integers(min_value=MIN_SEQUENCE_LENGTH, max_value=MAX_SEQUENCE_LENGTH))
    repeat_count = length - 1
    steps = tuple(
        draw(st.lists(st.sampled_from(STEP_KINDS), min_size=repeat_count, max_size=repeat_count))
    )
    return _RetrySequence(
        key=draw(
            st.text(
                alphabet=st.characters(
                    min_codepoint=33, max_codepoint=126, blacklist_characters=" "
                ),
                min_size=1,
                max_size=48,
            ).filter(lambda text: text.strip() == text and text.strip() != "")
        ),
        flavour=flavour,
        side=draw(st.sampled_from(("buy", "sell"))),
        quantity=draw(st.sampled_from(SUBMIT_QUANTITIES)),
        limit_price=draw(st.sampled_from(SUBMIT_LIMIT_PRICES)),
        close=draw(st.sampled_from(SUBMIT_CLOSES)),
        fee_rate=draw(st.sampled_from(SUBMIT_FEE_RATES)),
        steps=steps,
    )


def _first_intent(seq: _RetrySequence) -> Dict[str, Any]:
    """The intent the FIRST submission of the sequence carries."""
    if seq.flavour == "limit":
        return _intent(
            side=seq.side,
            order_type="limit",
            quantity=seq.quantity,
            limit_price=seq.limit_price,
            idempotency_key=seq.key,
        )
    return _intent(
        side=seq.side,
        quantity=seq.quantity,
        idempotency_key=seq.key,
    )


def _submit_for(
    supabase: Any,
    session: Dict[str, Any],
    account_id: str,
    config: sim.SessionConfig,
    intent: Mapping[str, Any],
    seq: _RetrySequence,
) -> sim.SubmitOutcome:
    """One submission, with the market flavour's event supplied by the caller (as P-22 does)."""
    kwargs: Dict[str, Any] = {"config": config, "intent": dict(intent)}
    if seq.flavour != "limit":
        from tests.test_paper_order_lifecycle_writes import _event

        kwargs["latest_event"] = _event(close=seq.close, source_event_id="evt-submit")
    return _submit(supabase, session, account_id, **kwargs)


def _assert_the_durable_index_refuses_a_second_order_row(
    supabase: Any, seq: _RetrySequence, account_id: str
) -> None:
    """Requirement 2.11's uniqueness constraint, asked directly of the Persistence_Layer.

    ``submit_intent``'s idempotency probe is a READ inside the attempt, and a read can be
    overtaken by a concurrent first request. What makes "one order per key per session" true
    whatever the interleaving is ``uq_paper_order_idem`` itself
    (``backend_app/migrations/009_paper_trading.sql:738``), so the index is asked directly rather
    than inferred from the probe having answered - copied from
    ``test_paper_idempotence.py``'s function of the same purpose and the same reasoning.
    """
    before = _snapshot(supabase)
    with pytest.raises(repo.PaperConcurrencyConflict):
        repo.insert_order(
            supabase,
            account_id=account_id,
            user_id="11111111-1111-4111-8111-111111111111",
            session_id=SESSION,
            symbol="BTC/USDT",
            side="buy",
            order_type="market",
            quantity="1",
            reference_price="100",
            fingerprint="fp-a-second-first-request",
            idempotency_key=seq.key,
            order_state=PaperOrderState.CREATED,
        )
    assert _snapshot(supabase) == before, (
        f"task 12.1 (Requirement 2.11): uq_paper_order_idem refused the second "
        f"(session_id, idempotency_key) row, but something was persisted anyway. "
        f"Sequence: {seq!r}"
    )


P12_1_FLOORS: Dict[str, int] = {
    "examples": EXAMPLES,
    # A fair 50/50 draw over 100 examples: a floor at 30% is a real guard against a broken
    # sampler without being a coin-toss failure on an honest one.
    "flavour_limit": int(EXAMPLES * 0.2),
    "flavour_market": int(EXAMPLES * 0.2),
    "step_retry": int(EXAMPLES * 0.5),
    "step_restart": int(EXAMPLES * 0.5),
    "sequence_length_2": 1,
    "sequence_length_at_or_above_5": int(EXAMPLES * 0.2),
    "sequence_length_10": 1,
    "final_state_ACCEPTED": int(EXAMPLES * 0.15),
    "final_state_FILLED": int(EXAMPLES * 0.15),
}

P12_1_LABELS: Dict[str, str] = {
    "sequence_length_at_or_above_5": "a sequence of 5 or more submissions was generated",
    "sequence_length_10": "the maximum sequence length (10) was generated",
}

#: Buckets whose floor is an EXISTENCE claim about one particular drawn value, as opposed to a
#: claim about the run's distribution. The distinction is the whole of this file's fix for
#: TEST-05 and is derived from the floors rather than restated, so a newly added floor-1 bucket
#: joins this set automatically and
#: :func:`test_every_existence_floor_is_forced_rather_than_left_to_the_draw` immediately demands
#: forced coverage for it.
#:
#: WHY THE TWO KINDS CANNOT BE GUARDED THE SAME WAY
#: ------------------------------------------------
#: Hypothesis's generation is tree-guided: it draws a novel PREFIX and then explores many
#: children beneath it, varying the LATER draws while the EARLIER ones stay put. In this
#: generator the later draws are the 1-9 element ``steps`` list and the filtered ``key`` text,
#: which is a deep sub-tree, so one 100-example run visits only a handful of distinct
#: ``(flavour, length)`` prefixes - measured at a mean of 8.33 of the 9 possible lengths, min 6
#: (``staging/probe_test05_prefix_autocorrelation.py``). ``length`` is therefore NOT redrawn 100
#: independent times, and an existence floor on one specific length is a bet on which prefixes
#: this run happened to visit: ``sequence_length_10`` was measured at 0 in 12 of 150 runs (~8%),
#: in ISOLATION as well as in a full suite (``staging/probe_test05_census_distribution.py``,
#: ``staging/probe_test05_integers_bimodality.py``). A distribution floor survives the same
#: mechanism because it is satisfied by any of many prefixes rather than one: measured minima
#: over 60 runs are limit 31 / market 21 against floor 20, retry 191 / restart 182 against floor
#: 50, and >=5 51 against floor 20 - between 1.05x and 3.8x headroom, never once short.
#:
#: So the existence floors are FORCED (:data:`DETERMINISTIC_COVERAGE`) and the distribution
#: floors are left to the generated run, which is where they still mean something: forcing them
#: would make them assertions about this file's own literals rather than about the sampler.
EXISTENCE_FLOORS: Tuple[str, ...] = tuple(
    sorted(key for key, floor in P12_1_FLOORS.items() if floor <= 1)
)


# ══════════════════════════════════════════════════════════════════════════════════════════
#  DETERMINISTIC COVERAGE OF THE EXISTENCE FLOORS - forced, not hoped for (TEST-05's fix)
# ══════════════════════════════════════════════════════════════════════════════════════════
#
# WHAT WENT WRONG, MEASURED RATHER THAN INFERRED
# ----------------------------------------------
# ``sequence_length_10`` is an existence floor on one drawn value, and the generated run does
# not guarantee it. Measured over independent 100-example runs of this very generator:
#
#   * ``sequence_length_10 == 0`` in 12 of 150 runs (~8%), the census's exact failure mode,
#     and it happens IN ISOLATION as readily as in a full suite - so the Phase 6-9 attribution
#     to Hypothesis harvesting constants out of a larger ``sys.modules`` was not the cause.
#     Isolation runs passing 8/8 was a 1-in-3 coincidence at this flake rate, not evidence.
#   * the run visits a mean of 8.33 of the 9 possible lengths, min 6 - lengths are drawn in
#     long runs of one value, not independently, because Hypothesis explores many children
#     under one novel prefix while the early draws (``flavour``, ``length``) stay fixed and the
#     late ones (the 1-9 element ``steps`` list, the filtered ``key`` text) vary.
#   * ``st.integers(2, 10)`` ALONE is not the culprit: driven on its own, Hypothesis exhausts
#     all nine values and draws 10 exactly once in 100% of runs. The shortfall is a property
#     of the composite's prefix structure, not of the integer distribution.
#   * raising ``max_examples`` is not a fix: at 200 the shortfall was still 1/30, and at 400 it
#     was merely rarer, at 4x the runtime. ``max_examples`` is therefore left at 100.
#
# Evidence: ``staging/probe_test05_census_distribution.py``,
# ``staging/probe_test05_integers_bimodality.py``,
# ``staging/probe_test05_prefix_autocorrelation.py``, and
# ``staging/PHASE10_TEST05_IDEMPOTENCY_REPORT.md``.
#
# WHAT THE FIX IS, AND WHAT IT IS NOT
# -----------------------------------
# The sequences below are supplied to the property as ``@example`` cases, so Hypothesis runs
# them IN ADDITION to - and before - the 100 generated ones (verified: with
# ``max_examples=1`` all three still run, 4 invocations total,
# ``staging/probe_test05_forced_examples_run.py``). Every existence floor is then satisfied by
# construction on every run, in every lane, at any seed.
#
# No floor is lowered, no assertion is relaxed, no test is skipped, ``max_examples`` is
# unchanged, and nothing is mocked: a forced sequence goes through exactly the same
# :func:`_assert_the_sequence_yields_exactly_one_order` - the same real
# ``paper_simulator.submit_intent``, the same persistence double, the same duplicate-answer and
# no-financial-movement oracles, the same direct ``uq_paper_order_idem`` probe - as a generated
# one. ``test_the_forced_coverage_sequences_are_legal_generator_draws`` pins that each one is a
# sequence the generator could itself have produced, so the forced cases cannot drift into a
# shape the property was never written for.
#
# The DISTRIBUTION floors (``flavour_*``, ``step_*``, ``sequence_length_at_or_above_5``,
# ``final_state_*``) are deliberately NOT forced - see :data:`EXISTENCE_FLOORS` for the measured
# headroom that makes them honest, and for why forcing them would turn a guard on the sampler
# into a guard on this file's own literals.
#
# Both flavours and both step kinds appear across the forced set, so the forced cases cannot
# tip a distribution floor on their own either way.

#: length 10 - the maximum, and the floor that actually failed.
FORCED_LEN_10 = _RetrySequence(
    key="forced-len10-" + "r" * 10, flavour="market", side="buy", quantity="1",
    limit_price="100", close="100", fee_rate="0.001",
    steps=("restart", "retry", "restart", "retry", "restart", "retry", "restart", "retry",
           "restart"),  # 9 repeats -> total length 10
)

#: length 5 - the ``sequence_length_at_or_above_5`` boundary itself.
FORCED_LEN_5 = _RetrySequence(
    key="forced-len5-restart", flavour="limit", side="sell", quantity="2",
    limit_price="120.50", close="100", fee_rate="0",
    steps=("retry", "restart", "retry", "restart"),  # 4 repeats -> total length 5
)

#: length 2 - the minimum, task 12.1's own floor, and the non-vacuity case.
FORCED_LEN_2 = _RetrySequence(
    key="forced-len2-retry", flavour="limit", side="buy", quantity="0.25",
    limit_price="50", close="50", fee_rate="0.0025",
    steps=("retry",),  # 1 repeat -> total length 2
)

#: The forced set. Module-level rather than local to the test so the two guards below can read
#: the same objects the property runs, and - via :func:`_forcing` - it IS what the property runs
#: rather than a second list that happens to agree: stacking ``@example`` by hand would let this
#: tuple and the decorators drift apart, and the guards would then be checking a tuple nobody
#: executes.
DETERMINISTIC_COVERAGE: Tuple[_RetrySequence, ...] = (
    FORCED_LEN_10,
    FORCED_LEN_5,
    FORCED_LEN_2,
)


def _forcing(property_function: Any) -> Any:
    """Register every :data:`DETERMINISTIC_COVERAGE` sequence as an ``@example`` on a property.

    Exactly equivalent to stacking ``@example(seq=...)`` for each one - which is what this
    replaces - but driven off the tuple, so the forced set and the cases Hypothesis actually
    runs cannot disagree. Applied BELOW ``@settings`` and ABOVE ``@given``, which is where a
    hand-written ``@example`` stack sits.
    """
    for seq in DETERMINISTIC_COVERAGE:
        property_function = example(seq=seq)(property_function)
    return property_function


def _census_buckets_for(seq: _RetrySequence) -> Dict[str, int]:
    """Every census bucket one sequence's SHAPE contributes, and how many times.

    The single declaration of "which buckets does this sequence count towards", so the property
    body and the coverage guards below read the same answer rather than two copies of it. Only
    shape-derived buckets live here; ``final_state_*`` is decided by what the simulator actually
    persisted and is marked by the body.
    """
    length = 1 + len(seq.steps)
    buckets: Dict[str, int] = {"examples": 1, f"flavour_{seq.flavour}": 1}
    for step in seq.steps:
        buckets[f"step_{step}"] = buckets.get(f"step_{step}", 0) + 1
    if length == MIN_SEQUENCE_LENGTH:
        buckets["sequence_length_2"] = 1
    if length >= 5:
        buckets["sequence_length_at_or_above_5"] = 1
    if length == MAX_SEQUENCE_LENGTH:
        buckets["sequence_length_10"] = 1
    return buckets


def _assert_the_sequence_yields_exactly_one_order(seq: _RetrySequence, recorder: Recorder) -> None:
    """Task 12.1's two claims, over one generated sequence: exactly one row, one answer."""
    for bucket, times in _census_buckets_for(seq).items():
        recorder.mark(bucket, times)

    supabase, session, account_id = _seed(capital=RICH)
    first_config = _config(fee_rate=Decimal(seq.fee_rate), slippage_rate=Decimal("0"))
    first_intent = _first_intent(seq)

    first = _submit_for(supabase, session, account_id, first_config, first_intent, seq)
    assert len(supabase.orders) == 1, (
        f"task 12.1: the FIRST submission of the sequence must persist exactly one order, found "
        f"{len(supabase.orders)}. Sequence: {seq!r}"
    )
    recorded_id = str(first.order["id"])
    recorded_state = str(supabase.orders[0]["order_state"])
    if f"final_state_{recorded_state}" in recorder.counts:
        recorder.mark(f"final_state_{recorded_state}")
    after_first = _snapshot(supabase)

    active_config = first_config
    active_intent: Mapping[str, Any] = first_intent
    length = 1 + len(seq.steps)

    for index, step in enumerate(seq.steps, start=2):
        context = f"step {index} of {length} ({step}) for sequence {seq!r}"

        if step == "retry":
            # The SAME config object and the SAME intent mapping the previous step used - a
            # caller resending its own unchanged request.
            step_config = active_config
            step_intent = active_intent
        else:
            # ``restart``: an INDEPENDENTLY rebuilt config and a COPY of the intent - nothing the
            # previous step held in memory is reused, which is the one thing a worker restart
            # genuinely changes on this stateless path.
            step_config = _config(fee_rate=Decimal(seq.fee_rate), slippage_rate=Decimal("0"))
            step_intent = dict(first_intent)

        outcome = _submit_for(supabase, session, account_id, step_config, step_intent, seq)
        active_config, active_intent = step_config, step_intent

        # Claim 1 (task 12.1): order count is invariant at exactly one.
        assert len(supabase.orders) == 1, (
            f"task 12.1: order count must stay at exactly one, found {len(supabase.orders)} "
            f"after {context}"
        )
        assert str(supabase.orders[0]["id"]) == recorded_id, (
            f"task 12.1: the single stored order is no longer the one the first submission "
            f"created, after {context}"
        )
        assert str(supabase.orders[0]["idempotency_key"]) == seq.key, (
            f"task 12.1: the single stored order no longer carries the generated key {seq.key!r} "
            f"after {context}"
        )

        # Claim 2 (task 12.1): every submission after the first is answered with THAT order.
        assert outcome.duplicate is True, (
            f"task 12.1 (Requirement 2.11): a repeated submission under an already-used key must "
            f"be reported as a duplicate, was not, after {context}"
        )
        assert str(outcome.order["id"]) == recorded_id, (
            f"task 12.1 (Requirement 2.11): the repeated submission answered order "
            f"{outcome.order['id']}, not the original {recorded_id}, after {context}"
        )
        assert str(outcome.order["order_state"]) == recorded_state, (
            f"task 12.1: the duplicate answer's state {outcome.order['order_state']!r} does not "
            f"match the recorded order's own state {recorded_state!r}, after {context}"
        )

        # Neither a retry nor a restart may move a balance, a position or the order itself - the
        # acknowledgement of an already-placed order changes nothing (Requirement 16.8's own
        # claim, held here across a drawn SEQUENCE rather than three hand-built variants).
        assert _snapshot(supabase) == after_first, (
            f"task 12.1: a repeated submission changed persisted state - an acknowledgement of "
            f"the original order may not move a balance, a position or an order - after {context}"
        )

    _assert_the_durable_index_refuses_a_second_order_row(supabase, seq, account_id)


def test_order_idempotence_under_generated_retry_and_restart_sequences(request: Any) -> None:
    """Task 12.1: over generated submit/retry/restart sequences carrying one idempotency key,
    order count is invariant at exactly one, and every submission after the first is answered
    with that same order.

    Extends ``test_paper_idempotence.py``'s ``test_p22_duplicate_order_intent_yields_one_order``
    (Requirements 16.8, 16.11, 16.15): P-22 forces three hand-built repeat kinds against a single
    first intent; this property instead DRAWS the retry/restart sequence itself - its length (2 to
    10, per task 12.1), which of the two step kinds appears at each position, and how many times
    each does - so "the invariant holds for the sequences P-22 already tried" is generalised to
    "the invariant holds for the sequence SHAPE, drawn". A ``restart`` step rebuilds the session
    configuration independently and copies the intent, rather than reusing either object a prior
    step held, because ``paper_simulator.submit_intent`` carries no in-process state between calls
    (see the module docstring) - so a ``restart`` step is the case that would expose a correctness
    dependency on something a crashed process could not have kept.

    The oracle is the row the first submission persisted, read directly off the Persistence_Layer
    double, never off any outcome's own self-report. Requirement 2.11's ``uq_paper_order_idem`` is
    additionally asked directly at the end of every sequence, because the probe inside
    ``submit_intent`` is a read and a read can be overtaken.

    Paper and simulation only: no live exchange adapter is constructed or imported anywhere in this
    module.

    Partly proves Requirement 1.11 for the paper path; ``tests/crash_recovery/
    test_worker_crash_mid_submission.py`` already proves the live path (see the module docstring).

    **Validates: Requirements 1.11, 2.11**
    """
    recorder = Recorder("task-12.1", P12_1_FLOORS, P12_1_LABELS)

    @PROPERTY_SETTINGS
    @_forcing
    @given(seq=submit_retry_restart_sequences())
    def check(seq: _RetrySequence) -> None:
        recorder.start()
        _assert_the_sequence_yields_exactly_one_order(seq, recorder)
        recorder.finish()

    try:
        with publish_hypothesis_statistics(request.node):
            check()
    finally:
        repo.reset_persistence_probe()

    recorder.assert_not_vacuous()


def test_this_module_never_constructs_a_live_venue_adapter() -> None:
    """Pinned structurally, not just asserted in prose: paper/simulation only, per task 12.1.

    Asserted on the parsed imports and calls of this module's own source, rather than on the
    source text of ``paper_simulator`` (which is this file's premise, not its subject).
    """
    import ast
    import inspect
    from pathlib import Path

    path = Path(inspect.getsourcefile(_assert_the_sequence_yields_exactly_one_order) or "")
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))

    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported_names.add((node.module or "", alias.name))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported_names.add((alias.name, ""))

    live_markers = [
        (module, name)
        for module, name in imported_names
        if "live" in module.lower() or "exchange_adapter" in module.lower() or "venue" in module.lower()
    ]
    assert live_markers == [], (
        f"this module must drive the paper path only; found live/venue-looking imports: "
        f"{live_markers}"
    )


def test_every_existence_floor_is_forced_rather_than_left_to_the_draw() -> None:
    """TEST-05's fix, pinned: no existence floor may go back to being a bet on a draw.

    Reads :data:`EXISTENCE_FLOORS` off :data:`P12_1_FLOORS` rather than naming the buckets, so
    adding a new floor-1 bucket without forced coverage reds HERE - cheaply, deterministically -
    instead of flaking in a full-suite run months later, which is exactly how TEST-05 was found.

    Also pins the two things the forced coverage silently depends on: that ``Phase.explicit`` is
    still enabled on the property's settings (with it off, ``@example`` cases never run and the
    floors quietly go back to being a bet), and that ``max_examples`` was not raised instead of
    the shortfall being forced.
    """
    assert EXISTENCE_FLOORS, (
        "the census has no existence floor at all - either P12_1_FLOORS lost its floor-1 "
        "buckets or EXISTENCE_FLOORS no longer derives from it"
    )

    forced_counts: Dict[str, int] = {}
    for seq in DETERMINISTIC_COVERAGE:
        for bucket, times in _census_buckets_for(seq).items():
            forced_counts[bucket] = forced_counts.get(bucket, 0) + times

    unforced = {
        bucket: P12_1_FLOORS[bucket]
        for bucket in EXISTENCE_FLOORS
        if forced_counts.get(bucket, 0) < P12_1_FLOORS[bucket]
    }
    assert not unforced, (
        f"TEST-05: these census buckets are EXISTENCE claims about one drawn value, which a "
        f"tree-guided 100-example run does not guarantee, and nothing in "
        f"DETERMINISTIC_COVERAGE forces them: {unforced}. Add a forced sequence that produces "
        f"the case - do NOT lower the floor and do NOT raise max_examples. Forced census from "
        f"DETERMINISTIC_COVERAGE: {forced_counts}"
    )

    assert Phase.explicit in PROPERTY_SETTINGS.phases, (
        "the forced coverage is supplied as @example cases, so Phase.explicit must stay enabled "
        f"on PROPERTY_SETTINGS; phases are {PROPERTY_SETTINGS.phases}"
    )
    assert PROPERTY_SETTINGS.max_examples == EXAMPLES == 100, (
        "design.md requires at least 100 examples, and TEST-05 was NOT fixed by raising the "
        f"budget (measured: still short 1/30 runs at 200); max_examples is "
        f"{PROPERTY_SETTINGS.max_examples}"
    )


def test_the_forced_coverage_sequences_are_legal_generator_draws() -> None:
    """Every forced sequence is one the generator could itself have produced.

    This is what makes "the forced cases exercise the identical assertions, not a weaker path" a
    checked claim rather than a comment. Each field is asserted to come from the very pool
    :func:`submit_retry_restart_sequences` draws it from, so a later edit cannot quietly give a
    forced case a quantity, price, fee, side, flavour, step kind, length or key shape that the
    property was never written for - which would make the forced coverage cover the wrong thing.

    The key is additionally checked against the generator's own filter AND against
    ``chk_paper_order_idem_len`` (``backend_app/migrations/009_paper_trading.sql:737-738``,
    length 1-128), because an over-long forced key would be refused by the database rather than
    deduplicated by it.
    """
    assert DETERMINISTIC_COVERAGE, "the forced coverage set is empty"

    for seq in DETERMINISTIC_COVERAGE:
        where = f"forced sequence {seq!r}"

        assert seq.flavour in ORDER_FLAVOURS, f"{where}: flavour not in ORDER_FLAVOURS"
        assert seq.side in ("buy", "sell"), f"{where}: side is not a drawn side"
        assert seq.quantity in SUBMIT_QUANTITIES, f"{where}: quantity not in SUBMIT_QUANTITIES"
        assert seq.limit_price in SUBMIT_LIMIT_PRICES, f"{where}: limit not in SUBMIT_LIMIT_PRICES"
        assert seq.close in SUBMIT_CLOSES, f"{where}: close not in SUBMIT_CLOSES"
        assert seq.fee_rate in SUBMIT_FEE_RATES, f"{where}: fee_rate not in SUBMIT_FEE_RATES"

        assert all(step in STEP_KINDS for step in seq.steps), (
            f"{where}: a step is not one of STEP_KINDS {STEP_KINDS} - a forced sequence may not "
            f"contain a step kind the generator cannot draw"
        )
        length = 1 + len(seq.steps)
        assert MIN_SEQUENCE_LENGTH <= length <= MAX_SEQUENCE_LENGTH, (
            f"{where}: total length {length} is outside the generator's own "
            f"{MIN_SEQUENCE_LENGTH}-{MAX_SEQUENCE_LENGTH} range"
        )

        # The generator's own key constraints: printable non-space, 1-48 chars, strip-stable.
        assert 1 <= len(seq.key) <= 48, f"{where}: key length {len(seq.key)} outside 1-48"
        assert 1 <= len(seq.key) <= 128, (
            f"{where}: key length {len(seq.key)} violates chk_paper_order_idem_len (1-128)"
        )
        assert seq.key.strip() == seq.key != "", f"{where}: key is not strip-stable and non-empty"
        assert all(33 <= ord(ch) <= 126 for ch in seq.key), (
            f"{where}: key has a character the generator's alphabet cannot draw"
        )

    assert len({seq.key for seq in DETERMINISTIC_COVERAGE}) == len(DETERMINISTIC_COVERAGE), (
        "two forced sequences share an idempotency key; each forced case must be its own "
        "first submission under its own key"
    )
    # Both flavours and both step kinds present, so the forced set cannot tip a DISTRIBUTION
    # floor on its own in either direction.
    assert {seq.flavour for seq in DETERMINISTIC_COVERAGE} == set(ORDER_FLAVOURS)
    assert {step for seq in DETERMINISTIC_COVERAGE for step in seq.steps} == set(STEP_KINDS)


def test_the_sequence_generator_never_places_a_second_first_submission() -> None:
    """``steps`` may only ever be ``retry`` or ``restart`` - never ``submit`` again.

    A second ``submit`` in the tail would be a second FIRST submission under the same key with
    freshly-drawn parameters, which is a mismatch case outside this property's own stated scope
    (see the module docstring's gap 3) and would silently turn some fraction of examples into a
    409-conflict test wearing this property's name.
    """
    for value in STEP_KINDS:
        assert value in ("retry", "restart"), value
    assert "submit" not in STEP_KINDS
