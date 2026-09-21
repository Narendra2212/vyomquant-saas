"""
tests/property/test_paper_confluence.py - P-23.

Spec: marketplace-subscriptions-paper-trading task 25.13. ``design.md`` -> "Property-to-test
mapping". Requirements 16.10, 18.3.

THE PROPERTY LIVING HERE
------------------------
``test_p23_concurrent_intents_match_a_sequential_order``   (Requirements 16.10, 18.3)

Exactly one ``test_p{n}_`` function in the whole file, at module scope:
``tests/property/test_property_coverage.py`` discovers by ``ast.walk`` and descends into function
bodies, so a helper named ``test_p23_...`` inside one would claim P-23 twice.

WHAT THIS TRANSPORT ACTUALLY OFFERS - READ THIS BEFORE THE PROPERTY
------------------------------------------------------------------
``paper_simulator``'s section header on "what one transaction means over this transport" is the
premise of this whole file. Over PostgREST there is no ``BEGIN``, no ``ROLLBACK`` and no
``SELECT ... FOR UPDATE``. ``paper_repository.lock_account_for_update`` is a **read** that returns
the account's ``version``; every subsequent write goes through ``bump_version(expected_version=N)``,
whose ``.eq("version", N)`` matches zero rows if another writer moved the account in between. So a
concurrent submission resolves as exactly one of two things:

* **a serialised success** - the version predicate caught the lost update, the attempt was retried,
  and the retry read the moved row and completed; or
* **a 409** ``PAPER_CONCURRENCY_CONFLICT`` - three attempts all conflicted (Requirement 16.10's
  bound), and past the account UPDATE the failure is reported with ``partial_write = True`` and
  deliberately **not** retried, because a retry there would double-apply.

P-23 is therefore stated over the intents that **succeeded**. It does NOT assert that every
concurrently submitted intent succeeds - this transport does not offer that, and asserting it would
make the property either vacuous (by never producing a conflict) or flaky (by producing one and
calling it a failure). What it asserts instead is the whole of what confluence means here:

1. For the set of intents that succeeded, the resulting balances, positions and equity are
   **identical, as exact ``Decimal``**, to those produced by applying the same intents one at a
   time in **some** sequential order - and, because every sequential order of these intents
   produces one common result, to those produced by applying them in **any** sequential order.
2. No intent was applied twice: exactly one ``paper_orders`` row exists per idempotency key, and
   exactly one settled ``paper_fills`` row exists per fill event.
3. No intent was lost: every intent that reported success is present in the persisted state with
   the effect its sequential application would have had.
4. An intent that answered 409 was **not applied** - it moved no balance, opened no position,
   changed no equity - and the residue it did leave is asserted **exactly**, because over this
   transport there is one and it is not zero. See "THE 409 RESIDUE" below.

HOW GENUINE CONCURRENCY IS PRODUCED
-----------------------------------
``asyncio.gather`` would not do it. The Persistence_Layer double is synchronous and the injected
``sleep`` never yields, so every ``submit_intent`` coroutine runs to completion without ever
suspending: gathering them would serialise them and the version predicate would never fire.

The interleaving is driven through the double's own ``after_select`` seam instead - the same seam
``tests/test_paper_order_lifecycle_writes._version_racer`` uses, and for the same reason: it fires
once a read has completed and its rows are copied out, so the caller holds a correct image of a row
that has since moved. :class:`_Interleaver` installs itself there, counts the account reads issued
at each nesting depth, and at the scheduled read **runs another one of the generated intents to
completion** before letting the read return. The competing writer is therefore a real
``submit_intent`` against the same account, not a phantom that only bumps a number.

The nested submission cannot be driven by ``_run_coroutine``: a loop is already running, and
``loop.run_until_complete`` refuses to start a second one inside it. It is driven by
:func:`_drive_without_a_loop`, which steps the coroutine once and requires it to finish - see that
function for why one step is enough and what happens if the module ever gains a real await.
``asyncio.run`` appears nowhere; the depth-0 submissions all go through the harness's
``_run_coroutine`` on its private loop.

The schedule FORCES the second account read of the outer submission to be a switch point, which is
the read that immediately precedes the version-guarded UPDATE - so a conflict is not hoped for, it
is arranged, and :data:`P23_FLOORS` fails the run if one did not happen. It also forces the second
account read of the **nested** submission to be a switch point, so the competitor is itself
overtaken and a second, deeper conflict occurs. Everything else about the schedule is drawn.

THE ORACLE, AND WHY IT IS INDEPENDENT OF THE CODE UNDER TEST
-----------------------------------------------------------
Every sequential permutation. For the four intents that succeed, all ``4! = 24`` orders are
replayed one intent at a time on twenty-four fresh stores, with no interleaving and no conflict
anywhere; :func:`_sequential_oracle` asserts that all twenty-four produced **one common result**
and returns it. The concurrent run's state is compared against that. Nothing in the expectation is
computed by asking the simulator what it thinks happened concurrently: the answer is what the same
intents did when nothing raced.

The "one common result" assertion is not decoration. It is what makes the comparison meaningful: if
these intents were order-dependent, "equals some sequential order" would be a much weaker claim
than "equals THE sequential result", and a bug that produced a different-but-also-reachable state
would pass. Order-independence is arranged rather than assumed - see
:func:`confluent_intent_sets`.

THE 409 RESIDUE, STATED RATHER THAN ASSERTED AROUND
--------------------------------------------------
"An intent that got a 409 left nothing behind" is **false** over this transport, and the property
says so precisely rather than pretending otherwise. Two shapes exist, and every example asserts
both, exactly:

* **A limit intent** exhausts in ``_lock_for_order``, which runs *after* the order is durable at
  ``ACCEPTED``. The 409 carries ``phase='ORDER_LOCK'`` and ``partial_write=True``, and the residue
  is one ``paper_orders`` row at ``ACCEPTED`` with **nothing locked**: no ledger row, no position,
  no equity point, and the account's four figures untouched.
* **A market intent** exhausts in ``apply_fill``, whose write ordering puts the fill row first
  because it is the idempotency anchor a retry resumes from. The residue is one ``paper_orders`` row
  at ``ACCEPTED`` **plus one unsettled ``paper_fills`` row** - unsettled meaning no
  ``paper_balance_events`` row carries its ``fill_id``, which is precisely how ``apply_fill``'s
  guard 2 recognises it and resumes rather than skipping. No money moved.

Both are asserted as equalities, not as "at most": a residue that grew a ledger row, a position or
an equity point would fail, and so would one that moved a single minor unit of any balance.

EVERY COMPARISON IS EXACT ``Decimal``
-------------------------------------
No ``pytest.approx``, no ``round()``, no tolerance. ``available_balance``, ``locked_balance``,
``realized_pnl`` and ``total_equity`` are compared with ``==`` on ``Decimal``, position sizes and
entry prices likewise, fees and slippage as the exact ``int`` minor units the columns store.
Requirement 18.3 says "exact decimal equality with zero tolerance", and a tolerance here would
make the whole property a statement about nothing.

WHAT IS COMPARED, AND WHAT CANNOT BE - INCLUDING ONE MEASURED FINDING
--------------------------------------------------------------------
Compared exactly, as ``Decimal`` and ``int``: ``available_balance``, ``locked_balance``,
``realized_pnl`` and ``total_equity``; every position's side, size, entry price, current price and
closed-ness; every order's state, side, type, quantity, filled quantity, limit price, rejection
reason and accumulated fee and slippage minor units; every settled fill's quantity, price, fee and
slippage; every closed trade; the number of ledger rows of each cause and the exact SUM of each of
its three delta columns; the number of equity snapshots and the equity the session ended at.

Not compared, and why:

* **Row identifiers and per-row ``updated_at``.** The double mints ids from its statement sequence
  and ``paper_repository`` stamps ``updated_at`` from the wall clock, so two runs differing in
  statement ORDER necessarily differ in both. That is the thing concurrency changes; it is not what
  confluence is about.
* **The ledger's per-row deltas, and the intermediate equity values.** This one was *measured*, not
  assumed, and it is worth stating plainly. A ``FILL`` row's ``available_delta`` is the residual of
  the position-value change - ``paper_accounting.apply_fill`` computes
  ``-(new_value - old_value) - fee`` with each value quantized to Minor_Units per Requirement 18.2 -
  so two same-side fills SPLIT one total movement differently depending on which arrived first: a
  0.5 and a 0.75 buy at 50.025 book ``(-25.01, -37.51)`` one way round and ``(-25.00, -37.52)`` the
  other, summing to the same ``-62.52`` either way. Every row is correct, every row's arithmetic is
  exact, and the endpoint is identical. The split is a property of the PATH, and confluence is a
  claim about the RESULT - so the ledger is compared by row count per cause and by exact column
  totals, and the equity series by count plus the final value, with
  :func:`_assert_the_equity_series_ends_where_the_account_stands` tying that final value to the
  account row inside each store. Comparing the split would assert something the requirements do not
  say and the arithmetic cannot give.
* **The running ``available_after`` / ``locked_after`` / ``realized_after`` columns**, for the same
  reason one step more obviously: they are the balance at a point on the path, and their endpoint is
  the account row, which is compared exactly.

GAPS LEFT OPEN
--------------
1. **The single transaction of Requirement 24.6 is not achievable over this transport**, so
   Requirement 16.10's "roll back the whole transaction leaving no partial write" is met by write
   *ordering* plus a refusal to retry past the money, not by a rollback. The residue above is the
   measured cost of that, and it is reported in the 409's ``details`` rather than hidden. Closing
   it needs a database function or a direct connection, which is a spec decision and not this
   property's to make.
2. **The interleaving is deterministic given the drawn schedule.** It is a real race in the sense
   that matters - one submission's statements straddle another's and the version predicate catches
   the lost update - but it is not a thread race, and no ordering this schedule cannot express is
   explored. A true multi-worker test needs a real database; Requirement 17.3's two-instance claim
   is task 33.x's.
3. **Only same-side intents on one symbol are generated.** Mixing a buy and a sell on one symbol is
   genuinely order-dependent through the cost basis, so "one common result" would be false for it
   and the oracle would have nothing to compare against. The order-dependence of a reversal is
   P-31's subject (the reference-ledger agreement) and P-26's (cost basis), not confluence's.
"""

from __future__ import annotations

import itertools
from decimal import Decimal
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Sequence, Set, Tuple

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from backend_app.backend.paper import paper_repository as repo
from backend_app.backend.paper import paper_simulator as sim
from backend_app.backend.paper.errors import PAPER_CONCURRENCY_CONFLICT
from backend_app.backend.paper.paper_order_state import PaperOrderState

# ── The census, written once for the paper property modules that need it. ─────────────────
from tests.property.paper_census import Recorder, publish_hypothesis_statistics

# ── The shared harness. A second one would be a second account of what a session is. ──────
from tests.test_paper_order_lifecycle_writes import (
    _config,
    _event,
    _intent,
    _run_coroutine,
    _seed,
    _Sleeps,
    _snapshot,
    _version_racer,
)

#: ``design.md § Property-based testing configuration``: at least 100 examples, no per-example
#: deadline. One example drives twenty-four sequential permutations plus two interleaved runs plus
#: two starved submissions - a hundred-odd real ``submit_intent`` calls - so ``too_slow`` is
#: suppressed rather than the example count being cut.
EXAMPLES = 100

PROPERTY_SETTINGS = settings(
    max_examples=EXAMPLES,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)

#: The capital every run starts from. Large enough that no generated intent is ever an accidental
#: ``INSUFFICIENT_FUNDS``: a rejection for funds would depend on which intents had already been
#: applied, and an order-DEPENDENT rejection is exactly what the oracle cannot have.
RICH = Decimal("1000000")

#: Requirement 16.10's bound, written down here so the assertions below read the requirement rather
#: than the constant. :func:`test_the_written_down_retry_bound_is_the_modules_own` holds it.
REQUIREMENT_16_10_ATTEMPTS = 3


# ══════════════════════════════════════════════════════════════════════════
# DRIVING A NESTED SUBMISSION WITHOUT A SECOND EVENT LOOP
# ══════════════════════════════════════════════════════════════════════════


def _drive_without_a_loop(coro: Any) -> Any:
    """Run ``coro`` to completion synchronously, from inside a callback of a running loop.

    WHY THIS EXISTS
    ---------------
    The competing submission has to start **in the middle** of another submission's statement - that
    is what makes the race a race. The double is synchronous, so the seam that lets a test do this
    is a plain function call, and by the time it is called ``_run_coroutine``'s loop is already
    running: ``loop.run_until_complete`` refuses to start a second loop inside a running one, and
    ``asyncio.run`` would additionally close the loop the outer submission is standing on. Neither
    is usable here, and neither is used anywhere in this file.

    WHY ONE STEP IS ENOUGH
    ----------------------
    ``submit_intent`` and ``apply_fill`` await nothing that is not itself a coroutine over a
    synchronous Persistence_Layer double: every repository call is an ordinary function, and the one
    genuine suspension point - ``with_retries``'s backoff - is the injected ``sleep``, an
    ``async def`` that appends to a list and returns. Awaiting a coroutine does not yield to the
    loop, so the whole submission completes on the first ``send`` and raises ``StopIteration``
    carrying its result.

    If that ever stops being true - a real ``asyncio.sleep``, a real network call, an
    ``asyncio.Lock`` - ``send`` returns a future instead of raising, and this function fails loudly
    naming what was awaited rather than silently reporting half a submission as a whole one. That is
    the point of not writing ``while True: coro.send(None)`` here: a partial drive that looked
    complete would corrupt the very thing this property measures.
    """
    try:
        awaited = coro.send(None)
    except StopIteration as stop:
        return stop.value
    coro.close()
    raise AssertionError(
        "the nested submission suspended on a real awaitable "
        f"({awaited!r}), so it cannot be driven from inside the double's seam. Something in "
        "paper_simulator now awaits something other than an injected sleep or another coroutine "
        "over the synchronous double; this property needs a different interleaving mechanism, not "
        "a partial drive that looks complete."
    )


# ══════════════════════════════════════════════════════════════════════════
# THE INTENTS, AND WHY THEY ARE ORDER-INDEPENDENT
# ══════════════════════════════════════════════════════════════════════════

#: The four intents submitted concurrently and expected to succeed, plus the two that are starved
#: into a 409. Names, not indices, so a failure message says which intent it is talking about.
#:
#: ``limit``     a resting limit order. Locks funds through ``_lock_for_order``, which is a
#:               version-guarded UPDATE and therefore a conflict site.
#: ``market``    a market order. Fills on acceptance through ``apply_fill``, which is the other
#:               version-guarded UPDATE and the other conflict site.
#: ``limit2``    a second money-writer, so the NESTED submission can also be overtaken. With only
#:               two money-writers and one of them outside, the competitor would never be raced.
#: ``rejected``  a statically rejected intent. Writes two ``paper_orders`` statements and touches
#:               no balance, so it is a distinct intent that cannot conflict - which is worth
#:               having in the set precisely because it must not be lost either.
SUCCEEDING_NAMES: Tuple[str, ...] = ("limit", "market", "limit2", "rejected")

#: The two intents each run starves into ``PaperConcurrencyExhausted``, one per residue shape.
STARVED_NAMES: Tuple[str, ...] = ("starved_limit", "starved_market")

#: Quantities. Five distinct values are drawn from this pool, so every intent carries a different
#: fingerprint and the set is genuinely a set of DISTINCT intents (task 25.13's word).
QUANTITY_POOL: Tuple[str, ...] = (
    "0.25", "0.5", "0.75", "1", "1.5", "2", "3", "0.125",
)

#: Limit prices. Three distinct values are drawn. Two decimals at most - ``price_precision`` is 2.
LIMIT_PRICE_POOL: Tuple[str, ...] = ("50", "60", "96.25", "100", "120.50", "200")

#: Market closes, for the reference price a market order is priced from.
CLOSES: Tuple[str, ...] = ("50", "100", "96.25")

#: Session fee rates. Zero is included: confluence is not a claim about fees.
FEE_RATES: Tuple[str, ...] = ("0", "0.001", "0.0025")

#: Slippage rates. Only the funds allowance uses this; the recorded slippage is
#: ``|price - reference|``.
SLIPPAGE_RATES: Tuple[str, ...] = ("0", "0.0005")

#: The four Requirement 16.5 rejections the ``paper_orders`` CHECK constraints PERMIT, so the
#: ``rejected`` intent leaves a persisted ``REJECTED`` row that the oracle can compare.
REJECTED_INTENTS: Tuple[Tuple[str, Dict[str, Any]], ...] = (
    ("SYMBOL_NOT_VALIDATED", {"symbol": "ETH/USDT", "quantity": "0.5"}),
    ("QUANTITY_ABOVE_MAX", {"quantity": "2000"}),
    ("QUANTITY_PRECISION", {"quantity": "0.000000001"}),
    ("LIMIT_PRICE_PRECISION", {"order_type": "limit", "limit_price": "100.001"}),
)

#: The account read at which a submission is overtaken.
#:
#: Every attempt of ``submit_intent``, ``_lock_for_order`` and ``apply_fill`` takes **exactly one**
#: account read - ``lock_account_for_update`` - so a submission's reads number its phases. Read 1 is
#: ``submit_intent``'s own attempt, which for both a limit and a market order writes only
#: ``paper_orders`` and therefore has no version predicate to lose. Read 2 is the one immediately
#: before the version-guarded UPDATE: ``_lock_for_order``'s for a limit order,
#: ``apply_fill``'s for a market one. Forcing 2 is what makes a conflict ARRANGED rather than hoped
#: for.
FORCED_SWITCH = 2

#: Extra switch points may only come AFTER :data:`FORCED_SWITCH`, never before it.
#:
#: This is not tidiness. A release at read 1 would move the account *before* the submission's read
#: 2, so read 2 would return the moved row, the version predicate would be satisfied and the
#: submission would sail through - and the conflict the example exists to produce would not happen.
#: Reads 3 and 4 are the retries, where a further release conflicts again if anything is still
#: queued. So the extras are drawn from above 2 and the schedule stays generated without the forced
#: conflict becoming conditional on the draw.
EXTRA_SWITCH_FLOOR = FORCED_SWITCH + 1


class _ConfluenceCase:
    """One generated set of distinct intents, and the schedule that interleaves them."""

    __slots__ = (
        "key_suffix",
        "side",
        "quantities",
        "limit_prices",
        "close",
        "fee_rate",
        "slippage_rate",
        "rejected_intent",
        "limit2_order_type",
        "switch_at",
        "nested_switch_at",
    )

    def __init__(
        self,
        key_suffix: str,
        side: str,
        quantities: Sequence[str],
        limit_prices: Sequence[str],
        close: str,
        fee_rate: str,
        slippage_rate: str,
        rejected_intent: Tuple[str, Dict[str, Any]],
        limit2_order_type: str,
        switch_at: FrozenSet[int],
        nested_switch_at: FrozenSet[int],
    ) -> None:
        self.key_suffix = key_suffix
        self.side = side
        self.quantities = tuple(quantities)
        self.limit_prices = tuple(limit_prices)
        self.close = close
        self.fee_rate = fee_rate
        self.slippage_rate = slippage_rate
        self.rejected_intent = rejected_intent
        self.limit2_order_type = limit2_order_type
        self.switch_at = switch_at
        self.nested_switch_at = nested_switch_at

    def key(self, name: str) -> str:
        return f"{name}-{self.key_suffix}"

    def intent(self, name: str) -> Dict[str, Any]:
        """The intent named ``name``. Distinct from every other in quantity, hence in fingerprint."""
        quantity_for = dict(zip(("limit", "market", "limit2", "starved_limit",
                                 "starved_market"), self.quantities))
        if name == "limit":
            return _intent(
                side=self.side,
                order_type="limit",
                quantity=quantity_for["limit"],
                limit_price=self.limit_prices[0],
                idempotency_key=self.key(name),
            )
        if name == "market":
            return _intent(
                side=self.side,
                quantity=quantity_for["market"],
                idempotency_key=self.key(name),
            )
        if name == "limit2":
            payload: Dict[str, Any] = {
                "side": self.side,
                "quantity": quantity_for["limit2"],
                "idempotency_key": self.key(name),
                "order_type": self.limit2_order_type,
            }
            if self.limit2_order_type == "limit":
                payload["limit_price"] = self.limit_prices[1]
            return _intent(**payload)
        if name == "rejected":
            overrides = dict(self.rejected_intent[1])
            overrides.setdefault("side", self.side)
            return _intent(idempotency_key=self.key(name), **overrides)
        if name == "starved_limit":
            return _intent(
                side=self.side,
                order_type="limit",
                quantity=quantity_for["starved_limit"],
                limit_price=self.limit_prices[2],
                idempotency_key=self.key(name),
            )
        if name == "starved_market":
            return _intent(
                side=self.side,
                quantity=quantity_for["starved_market"],
                idempotency_key=self.key(name),
            )
        raise AssertionError(f"unknown intent name {name!r}")

    def __repr__(self) -> str:  # pragma: no cover - shrinker output
        return (
            f"_ConfluenceCase(keys=*-{self.key_suffix!r}, side={self.side!r}, "
            f"quantities={self.quantities}, limits={self.limit_prices}, close={self.close!r}, "
            f"fee={self.fee_rate!r}, slip={self.slippage_rate!r}, "
            f"rejected={self.rejected_intent[0]!r}, limit2={self.limit2_order_type!r}, "
            f"switch_at={sorted(self.switch_at)}, nested={sorted(self.nested_switch_at)})"
        )


@st.composite
def confluent_intent_sets(draw: Any) -> _ConfluenceCase:
    """Six DISTINCT intents against one Paper_Account, and the schedule that races four of them.

    WHY THE SET IS ORDER-INDEPENDENT, AND WHY THAT IS ARRANGED RATHER THAN HOPED FOR
    ------------------------------------------------------------------------------
    The oracle is "every sequential permutation yields one common result", which is a fact about
    these intents and not about every conceivable set. Three constraints make it true, each for a
    reason:

    * **One side per example, drawn.** A buy and a sell on one symbol are genuinely
      order-dependent: the sell's realized PnL is computed against the cost basis the buys before it
      established, so ``buy, sell`` and ``sell, buy`` end in different places. Same-side fills merge
      into one position at a weighted average entry price, which is commutative.
    * **One capital large enough for all of them.** An order-dependent ``INSUFFICIENT_FUNDS``
      rejection would make the sequential result depend on the order, so :data:`RICH` is far above
      anything the pools can require.
    * **No resting order is ever triggered.** ``check_resting_orders`` is not called here, so a
      limit order rests and locks and nothing else. A fill that depended on which candle arrived
      when is P-31's subject.

    Under those three, the arithmetic is commutative: a lock moves cash between two columns, a
    same-side fill merges into the position at a weighted average, and a rejection writes two order
    rows and touches nothing. The property nevertheless ASSERTS the common result over all
    twenty-four permutations rather than relying on this paragraph - if any of the three ever stops
    holding, the assertion says so instead of the comparison quietly weakening.

    WHAT IS FORCED, AND WHY
    -----------------------
    ``switch_at`` always contains :data:`FORCED_SWITCH`, the account read immediately before the
    outer submission's version-guarded UPDATE, so a genuine conflict occurs on **every** example - a
    run in which nothing ever conflicted proves nothing about confluence. ``nested_switch_at``
    always contains it too, so the competitor is itself overtaken and a second, deeper conflict
    occurs. Both sets additionally draw extra switch points, so which reads are switch points
    beyond the forced one is generated; every quantity, price, rate, rejection reason, side and key
    is drawn.
    """
    quantities = draw(
        st.lists(st.sampled_from(QUANTITY_POOL), min_size=5, max_size=5, unique=True)
    )
    prices = draw(
        st.lists(st.sampled_from(LIMIT_PRICE_POOL), min_size=3, max_size=3, unique=True)
    )
    return _ConfluenceCase(
        key_suffix=draw(
            st.text(
                alphabet=st.characters(
                    min_codepoint=97, max_codepoint=122, whitelist_characters="0123456789"
                ),
                min_size=1,
                max_size=16,
            )
        ),
        side=draw(st.sampled_from(("buy", "sell"))),
        quantities=quantities,
        limit_prices=prices,
        close=draw(st.sampled_from(CLOSES)),
        fee_rate=draw(st.sampled_from(FEE_RATES)),
        slippage_rate=draw(st.sampled_from(SLIPPAGE_RATES)),
        rejected_intent=draw(st.sampled_from(REJECTED_INTENTS)),
        limit2_order_type=draw(st.sampled_from(("limit", "market"))),
        switch_at=frozenset({FORCED_SWITCH})
        | frozenset(
            draw(
                st.sets(
                    st.integers(min_value=EXTRA_SWITCH_FLOOR, max_value=5), max_size=2
                )
            )
        ),
        nested_switch_at=frozenset({FORCED_SWITCH})
        | frozenset(
            draw(
                st.sets(
                    st.integers(min_value=EXTRA_SWITCH_FLOOR, max_value=4), max_size=1
                )
            )
        ),
    )


# ══════════════════════════════════════════════════════════════════════════
# THE OBSERVABLE STATE - WHAT "IDENTICAL" IS A CLAIM ABOUT
# ══════════════════════════════════════════════════════════════════════════


def _balances(supabase: Any) -> Dict[str, Decimal]:
    """The four figures Requirement 18.3's identity is written over, as exact ``Decimal``."""
    account = supabase.accounts[0]
    return {
        "available_balance": Decimal(str(account["available_balance"])),
        "locked_balance": Decimal(str(account["locked_balance"])),
        "realized_pnl": Decimal(str(account["realized_pnl"])),
        "total_equity": Decimal(str(account["total_equity"])),
    }


def _positions(supabase: Any) -> List[Tuple[Any, ...]]:
    """Every ``paper_positions`` row as exact values, in a stable order."""
    return sorted(
        (
            str(row["symbol"]),
            str(row["side"]),
            Decimal(str(row["size"])),
            Decimal(str(row["entry_price"])),
            Decimal(str(row["current_price"])) if row.get("current_price") is not None else None,
            row["closed_at"] is not None,
        )
        for row in supabase.positions
    )


def _key_by_order_id(supabase: Any) -> Dict[str, Optional[str]]:
    """``paper_orders.id -> idempotency_key``, so a fill can be named by the intent that caused it."""
    return {
        str(row["id"]): None if row.get("idempotency_key") is None
        else str(row["idempotency_key"])
        for row in supabase.orders
    }


def _orders_for(supabase: Any, keys: FrozenSet[str]) -> List[Tuple[Any, ...]]:
    """Every ``paper_orders`` row whose key is in ``keys``, as exact values, keyed by that key."""
    rows = [
        row
        for row in supabase.orders
        if row.get("idempotency_key") is not None and str(row["idempotency_key"]) in keys
    ]
    projected = [
        (
            str(row["idempotency_key"]),
            str(row["order_state"]),
            str(row["side"]),
            str(row["order_type"]),
            Decimal(str(row["quantity"])),
            Decimal(str(row["filled_quantity"])),
            None if row.get("limit_price") is None else Decimal(str(row["limit_price"])),
            None if row.get("rejection_reason") is None else str(row["rejection_reason"]),
            int(row.get("fee_minor") or 0),
            int(row.get("slippage_minor") or 0),
        )
        for row in rows
    ]
    # Sorted on the KEY alone: the tuples carry ``None`` in two slots and a plain ``sorted`` would
    # compare ``None`` against a ``Decimal`` if two rows ever tied on everything before it.
    return sorted(projected, key=lambda projection: projection[0])


def _settled_fill_ids(supabase: Any) -> Set[str]:
    """The ``paper_fills.id`` values a ``FILL`` ledger row carries.

    ``apply_fill``'s own definition of "this fill's money moved": the ledger row is written after
    the account UPDATE and carries ``fill_id``, so a fill row that appears here is settled and one
    that does not is a previous attempt that stopped between the two. Reading it the module's way
    is deliberate - the residue assertions are about that exact distinction.
    """
    return {
        str(row["fill_id"])
        for row in supabase.balance_events
        if str(row.get("cause")) == "FILL" and row.get("fill_id") is not None
    }


def _settled_fills_for(supabase: Any, keys: FrozenSet[str]) -> List[Tuple[Any, ...]]:
    """Every SETTLED ``paper_fills`` row belonging to an order whose key is in ``keys``."""
    settled = _settled_fill_ids(supabase)
    names = _key_by_order_id(supabase)
    return sorted(
        (
            str(names.get(str(row["order_id"]))),
            Decimal(str(row["quantity"])),
            Decimal(str(row["price"])),
            int(row.get("fee_minor") or 0),
            int(row.get("slippage_minor") or 0),
        )
        for row in supabase.fills
        if str(row["id"]) in settled and names.get(str(row["order_id"])) in keys
    )


def _unsettled_fills(supabase: Any) -> List[Dict[str, Any]]:
    """Every ``paper_fills`` row no ledger row carries - the market 409's residue."""
    settled = _settled_fill_ids(supabase)
    return [dict(row) for row in supabase.fills if str(row["id"]) not in settled]


def _ledger_shape(supabase: Any) -> Dict[str, Any]:
    """The ledger as its row count per cause and the exact SUM of each delta column.

    WHY NOT THE PER-ROW DELTAS, AND WHY THAT IS NOT A DEFECT
    -------------------------------------------------------
    A ``FILL`` row's ``available_delta`` is the *residual* of the position-value change:
    ``paper_accounting.apply_fill`` computes ``-(new_value - old_value) - fee`` where each value is
    quantized to the currency's Minor_Units (Requirement 18.2). Two same-side fills therefore SPLIT
    the same total movement differently depending on which of them arrived first - measured here, a
    0.5 and a 0.75 buy at 50.025 book ``(-25.01, -37.51)`` one way round and ``(-25.00, -37.52)``
    the other. Both are correct: each row describes the step it belongs to, at the precision the
    requirement mandates, and each row's own arithmetic is exact.

    So the per-row split is a property of the PATH and not of the result, and confluence is a claim
    about the result. What must be order-independent, and is asserted here, is the number of ledger
    rows of each kind and the exact total each column accounts for - which is the same statement as
    "the balances ended in the same place, and the ledger explains all of it".

    The running ``available_after`` / ``locked_after`` / ``realized_after`` columns are excluded for
    the same reason, one step more obviously: they are the running balance at that point in the path.
    Their endpoint is the account row, which IS compared exactly.
    """
    causes: Dict[str, int] = {}
    totals = {
        column: Decimal("0") for column in ("available_delta", "locked_delta", "realized_delta")
    }
    for row in supabase.balance_events:
        cause = str(row["cause"])
        causes[cause] = causes.get(cause, 0) + 1
        for column in totals:
            totals[column] += Decimal(str(row[column]))
    return {"causes": sorted(causes.items()), "totals": totals}


def _equity_shape(supabase: Any) -> Dict[str, Any]:
    """The equity series as its row count and its final ``total_equity``.

    One snapshot per applied fill (Requirement 18.11), so the count is what a lost or doubled fill
    would change - and the last one's ``total_equity`` is the equity the session ended at, which is
    what "the resulting equity" means. The intermediate values are path-dependent for exactly the
    reason :func:`_ledger_shape` records; the ``ORDER_LOCK`` that may follow the last fill moves cash
    between two columns whose sum is unchanged, so the last snapshot's total is still the final one -
    which :func:`_assert_the_equity_series_ends_where_the_account_stands` checks rather than assumes.
    """
    rows = list(supabase.equity_snapshots)
    return {
        "count": len(rows),
        "final_total_equity": None if not rows else Decimal(str(rows[-1]["total_equity"])),
    }


def _assert_the_equity_series_ends_where_the_account_stands(supabase: Any, context: str) -> None:
    """The last persisted equity point is the account's current ``total_equity``.

    A within-store claim, so it holds whatever the application order was, and the thing that makes
    :func:`_equity_shape`'s ``final_total_equity`` a statement about the session's equity rather than
    about whichever snapshot happened to be written last.
    """
    rows = list(supabase.equity_snapshots)
    assert rows, f"no equity snapshot was persisted at all, so no fill was applied. {context}"
    assert Decimal(str(rows[-1]["total_equity"])) == Decimal(
        str(supabase.accounts[0]["total_equity"])
    ), (
        f"P-23 (Requirement 18.3): the last persisted equity point "
        f"({rows[-1]['total_equity']}) is not the account's total_equity "
        f"({supabase.accounts[0]['total_equity']}). {context}"
    )


def _trades(supabase: Any) -> List[Tuple[Any, ...]]:
    """Every ``paper_trades`` row as exact values."""
    return sorted(
        (
            str(row["symbol"]),
            str(row["side"]),
            Decimal(str(row["quantity"])),
            Decimal(str(row["entry_price"])),
            Decimal(str(row["exit_price"])),
            Decimal(str(row["realized_pnl"])),
            int(row.get("fee_minor") or 0),
        )
        for row in supabase.trades
    )


def _state(supabase: Any, keys: FrozenSet[str]) -> Dict[str, Any]:
    """Everything P-23 compares, restricted to the orders and fills of ``keys``.

    Restricted, because the concurrent store additionally holds the 409'd intent's residue and the
    sequential stores do not. The residue is asserted separately and exactly; folding it into this
    comparison would make the comparison unable to say which of the two claims failed.
    """
    return {
        "balances": _balances(supabase),
        "positions": _positions(supabase),
        "orders": _orders_for(supabase, keys),
        "settled_fills": _settled_fills_for(supabase, keys),
        "ledger": _ledger_shape(supabase),
        "equity": _equity_shape(supabase),
        "trades": _trades(supabase),
    }


# ══════════════════════════════════════════════════════════════════════════
# THE SUBMISSIONS
# ══════════════════════════════════════════════════════════════════════════


def _submission(
    supabase: Any,
    session: Mapping[str, Any],
    account_id: str,
    config: sim.SessionConfig,
    case: _ConfluenceCase,
    name: str,
    sleeps: _Sleeps,
) -> Any:
    """The ``submit_intent`` coroutine for one named intent. Not awaited here.

    ``latest_event`` is passed for **every** intent, limit ones included, and that is not
    incidental: without it a limit order's ``ORDER_LOCK`` ledger row takes its ``occurred_at`` from
    ``_instant_of(account_row["updated_at"])``, which is a wall-clock value, and two stores would
    then differ in a column neither the requirement nor this property is about.
    """
    return sim.submit_intent(
        supabase,
        session,
        case.intent(name),
        config=config,
        account_id=account_id,
        latest_event=_event(close=case.close, source_event_id="evt-confluence"),
        sleep=sleeps,
    )


class _Outcome:
    """What one submission did: the outcome it returned, or the 409 it raised."""

    __slots__ = ("name", "outcome", "error")

    def __init__(
        self,
        name: str,
        outcome: Optional[sim.SubmitOutcome] = None,
        error: Optional[BaseException] = None,
    ) -> None:
        self.name = name
        self.outcome = outcome
        self.error = error

    @property
    def succeeded(self) -> bool:
        return self.error is None

    def __repr__(self) -> str:  # pragma: no cover - shrinker output
        return f"_Outcome({self.name!r}, {'ok' if self.succeeded else type(self.error).__name__})"


class _Interleaver:
    """The double's ``after_select`` seam, turned into a scheduler of competing submissions.

    Installed on ``FakeSupabase.after_select``, so it fires once an account read has completed and
    its rows are copied out - the caller now holds a correct image of a row that is about to move,
    which is exactly the race ``bump_version``'s version predicate exists to detect.

    Reads are counted PER NESTING DEPTH and the counter for a depth is reset each time a submission
    at that depth begins, so ``switch_at`` means "the Nth account read of THIS submission" and not
    "the Nth read in the whole run". Without that, releasing a competitor at read 1 would inflate
    the global count by however many reads the competitor made and the schedule would no longer name
    the read it intended - and read 2 is the one that matters, because it is the one immediately
    before the version-guarded UPDATE.

    Recursion is bounded by :attr:`pending`: each competitor is popped before it is run, so at most
    one release happens per queued intent however deep the schedule goes.
    """

    __slots__ = ("case", "run_nested", "pending", "depth", "counts", "released", "armed")

    def __init__(self, case: _ConfluenceCase, run_nested: Any) -> None:
        self.case = case
        self.run_nested = run_nested
        self.pending: List[str] = []
        self.depth = 0
        self.counts: Dict[int, int] = {}
        self.released: List[str] = []
        self.armed = True

    def plan_for(self, depth: int) -> FrozenSet[int]:
        return self.case.switch_at if depth == 0 else self.case.nested_switch_at

    def begin(self, depth: int) -> None:
        """A submission at ``depth`` is starting: its own read counter restarts at zero."""
        self.counts[depth] = 0

    def __call__(self, client: Any, query: Any) -> None:
        if not self.armed:
            return
        if query.table_name != repo.ACCOUNTS_TABLE or query.op != "select":
            return
        depth = self.depth
        self.counts[depth] = self.counts.get(depth, 0) + 1
        if self.counts[depth] not in self.plan_for(depth):
            return
        if not self.pending:
            return
        name = self.pending.pop(0)
        self.released.append(name)
        self.depth = depth + 1
        self.begin(self.depth)
        try:
            self.run_nested(name)
        finally:
            self.depth = depth


class _Race:
    """What one concurrent run produced, for the property to read."""

    __slots__ = ("supabase", "outcomes", "released", "sleeps", "starved_sleeps", "error")

    def __init__(
        self,
        supabase: Any,
        outcomes: Dict[str, _Outcome],
        released: List[str],
        sleeps: Dict[str, _Sleeps],
        starved_sleeps: _Sleeps,
        error: sim.PaperConcurrencyExhausted,
    ) -> None:
        self.supabase = supabase
        self.outcomes = outcomes
        self.released = released
        #: One backoff recorder PER RACED SUBMISSION, keyed by name, and a separate one for the
        #: starved submission. One shared recorder would be useless as evidence: the starved
        #: submission conflicts by construction, so "a real conflict occurred among the raced
        #: intents" would be satisfiable by the starved one alone, and "the OUTER submission
        #: retried and then succeeded" could not be told apart from "its competitor did".
        self.sleeps = sleeps
        self.starved_sleeps = starved_sleeps
        self.error = error

    def conflicted(self, name: str) -> bool:
        """Whether submission ``name`` slept a retry backoff - i.e. genuinely lost a race.

        A slept backoff is the only observable proof available here: for a limit order the conflict
        happens inside ``_lock_for_order``'s own retry loop and for a market order inside
        ``apply_fill``'s, neither of which is reflected in the ``SubmitOutcome.attempts`` that
        ``submit_intent`` returns.
        """
        return bool(self.sleeps[name].delays)


def _run_concurrently(case: _ConfluenceCase, outer: str, starved: str) -> _Race:
    """One concurrent run: four intents raced against each other, then one starved into a 409.

    ``outer`` is submitted on the harness's private loop; the other three sit in the interleaver's
    queue and are run to completion **inside** one of ``outer``'s account reads, which is what makes
    ``outer``'s statements straddle theirs. Whatever the schedule left queued is drained afterwards,
    still through the interleaver, so a queued intent is never silently dropped.

    ``starved`` is then submitted with ``_version_racer`` armed - a writer that moves the account on
    every read of it - so all three of Requirement 16.10's attempts conflict and the 409 is
    guaranteed rather than waited for. It changes only ``version``, so it moves no money and the
    succeeded set's state is unaffected by it.
    """
    supabase, session, account_id = _seed(capital=RICH)
    config = _config(
        fee_rate=Decimal(case.fee_rate), slippage_rate=Decimal(case.slippage_rate)
    )
    sleeps: Dict[str, _Sleeps] = {name: _Sleeps() for name in SUCCEEDING_NAMES}
    starved_sleeps = _Sleeps()
    outcomes: Dict[str, _Outcome] = {}

    def record(name: str, driver: Any) -> None:
        coroutine = _submission(
            supabase, session, account_id, config, case, name, sleeps[name]
        )
        try:
            outcomes[name] = _Outcome(name, outcome=driver(coroutine))
        except sim.PaperConcurrencyExhausted as exc:
            outcomes[name] = _Outcome(name, error=exc)

    interleaver = _Interleaver(case, lambda name: record(name, _drive_without_a_loop))
    interleaver.pending = [name for name in SUCCEEDING_NAMES if name != outer]
    # The two money-writers go to the front and ``rejected`` to the back, so the FIRST release is a
    # writer that moves the account (the one that makes ``outer`` conflict) and the SECOND, released
    # inside the first, is another (the one that makes the competitor conflict in turn).
    # ``rejected`` moves no money, so releasing it at a forced switch point would leave that switch
    # point with nothing to conflict against and the forced conflict would not happen.
    interleaver.pending.sort(key=lambda name: (name == "rejected", name != "limit2"))
    supabase.after_select = interleaver

    interleaver.begin(0)
    record(outer, _run_coroutine)
    while interleaver.pending:
        interleaver.begin(0)
        record(interleaver.pending.pop(0), _run_coroutine)

    interleaver.armed = False
    supabase.after_select = _version_racer
    with pytest.raises(sim.PaperConcurrencyExhausted) as caught:
        _run_coroutine(
            _submission(supabase, session, account_id, config, case, starved, starved_sleeps)
        )
    supabase.after_select = None
    return _Race(
        supabase, outcomes, list(interleaver.released), sleeps, starved_sleeps, caught.value
    )


def _run_sequentially(case: _ConfluenceCase, order: Sequence[str]) -> Any:
    """Apply ``order`` one intent at a time on a fresh store, with nothing racing anything."""
    supabase, session, account_id = _seed(capital=RICH)
    config = _config(
        fee_rate=Decimal(case.fee_rate), slippage_rate=Decimal(case.slippage_rate)
    )
    sleeps = _Sleeps()
    for name in order:
        _run_coroutine(
            _submission(supabase, session, account_id, config, case, name, sleeps)
        )
    assert sleeps.delays == [], (
        "a sequential replay slept a retry backoff, so something conflicted where nothing was "
        f"racing and the oracle is not the oracle: {sleeps.delays}"
    )
    return supabase


def _sequential_oracle(
    case: _ConfluenceCase, names: Sequence[str], keys: FrozenSet[str]
) -> Dict[str, Any]:
    """Every sequential permutation of ``names``, asserted to produce ONE common result.

    The oracle of task 25.13, computed by replaying the intents one at a time on a fresh store per
    permutation. Independent of the concurrent path by construction: no seam is installed, nothing
    is interleaved, and no retry is taken (asserted, in :func:`_run_sequentially`).

    The "one common result" assertion is the part that makes the comparison worth making. Without
    it, "the concurrent state equals SOME sequential order's" would be satisfiable by a set whose
    orders all differ, and a wrong-but-reachable state would pass.
    """
    results: Dict[Tuple[str, ...], Dict[str, Any]] = {}
    for permutation in itertools.permutations(names):
        supabase = _run_sequentially(case, permutation)
        _assert_the_equity_series_ends_where_the_account_stands(
            supabase, f"sequential order {permutation}. Case: {case!r}"
        )
        results[permutation] = _state(supabase, keys)

    reference_order = tuple(names)
    reference = results[reference_order]
    for permutation, result in results.items():
        if result == reference:
            continue
        differing = sorted(
            claim for claim in reference if result[claim] != reference[claim]
        )
        raise AssertionError(
            "P-23's oracle is not well defined: the sequential order "
            f"{permutation} produced a different result from {reference_order} in {differing}. "
            "These intents are order-DEPENDENT, so 'equals some sequential order' is a weaker "
            "claim than the property intends. Fix the generator's premise (see "
            "confluent_intent_sets), never the comparison. "
            + "; ".join(
                f"{claim}: {reference[claim]!r} against {result[claim]!r}" for claim in differing
            )
        )
    return reference


# ══════════════════════════════════════════════════════════════════════════
# THE 409 RESIDUE
# ══════════════════════════════════════════════════════════════════════════


def _assert_the_409_applied_nothing(
    supabase: Any,
    case: _ConfluenceCase,
    starved: str,
    error: sim.PaperConcurrencyExhausted,
) -> None:
    """The starved intent was not applied, and its residue is exactly the documented one.

    Both halves matter. "Not applied" is the property's claim: no ledger row, no position, no equity
    point, no settled fill, and - by the state comparison the caller makes - not a single minor unit
    of any balance. "Exactly the documented residue" is this transport's cost, asserted as an
    equality so that a residue which GREW would fail rather than be absorbed by an "at most".
    """
    key = case.key(starved)
    assert error.code == PAPER_CONCURRENCY_CONFLICT, (
        f"P-23 (Requirement 16.10): the exhausted retries must be reported as "
        f"{PAPER_CONCURRENCY_CONFLICT}, got {error.code}. Case: {case!r}"
    )
    assert error.http_status == 409, (
        f"P-23 (Requirement 16.10): a concurrency conflict is a 409, got {error.http_status}"
    )
    assert error.details["attempts"] == REQUIREMENT_16_10_ATTEMPTS, (
        f"P-23 (Requirement 16.10): at most three attempts, reported "
        f"{error.details['attempts']}. Case: {case!r}"
    )

    rows = [
        row
        for row in supabase.orders
        if row.get("idempotency_key") is not None and str(row["idempotency_key"]) == key
    ]
    assert len(rows) == 1, (
        f"P-23: the starved intent {starved} left {len(rows)} order rows under its key, not the "
        f"one durable ACCEPTED order this transport leaves. Case: {case!r}"
    )
    residue = rows[0]
    assert str(residue["order_state"]) == PaperOrderState.ACCEPTED.value, (
        f"P-23: the starved intent's residual order stands at {residue['order_state']}, not "
        f"ACCEPTED. Over this transport the accept committed before the money statement conflicted, "
        f"so ACCEPTED is the only state it can be in. Case: {case!r}"
    )
    assert Decimal(str(residue["filled_quantity"])) == Decimal("0"), (
        f"P-23: the starved intent's residual order carries a filled quantity of "
        f"{residue['filled_quantity']}, so a fill was accounted for on an intent that answered 409"
    )

    order_id = str(residue["id"])
    assert [
        row for row in supabase.balance_events if str(row.get("order_id") or "") == order_id
    ] == [], (
        f"P-23 (Requirement 16.10): the starved intent {starved} left a balance-event row, so its "
        f"money moved after all. Case: {case!r}"
    )
    settled = _settled_fill_ids(supabase)
    assert [
        row
        for row in supabase.fills
        if str(row["order_id"]) == order_id and str(row["id"]) in settled
    ] == [], (
        f"P-23: the starved intent {starved} left a SETTLED fill, so it was applied. Case: {case!r}"
    )

    unsettled = [
        row for row in _unsettled_fills(supabase) if str(row["order_id"]) == order_id
    ]
    if str(case.intent(starved).get("order_type") or "market") == "limit":
        assert error.details.get("phase") == "ORDER_LOCK", (
            f"P-23: a limit intent starved in ``_lock_for_order`` must name phase='ORDER_LOCK', "
            f"got {error.details.get('phase')!r}. Case: {case!r}"
        )
        assert error.details.get("partial_write") is True, (
            "P-23: the ORDER_LOCK exhaustion leaves a durable ACCEPTED order with nothing locked, "
            "and this transport offers no ROLLBACK for it, so partial_write must be reported True "
            f"rather than hidden; got {error.details.get('partial_write')!r}"
        )
        assert unsettled == [], (
            f"P-23: a limit intent starved before any fill, so it must leave no fill row at all; "
            f"found {unsettled}. Case: {case!r}"
        )
    else:
        assert error.details.get("operation") == "apply_fill", (
            f"P-23: a market intent starves in ``apply_fill``, got operation "
            f"{error.details.get('operation')!r}. Case: {case!r}"
        )
        assert len(unsettled) == 1, (
            f"P-23: a market intent starved in ``apply_fill`` leaves exactly ONE unsettled fill "
            f"row - the idempotency anchor a retry resumes from, written before the money and "
            f"never twice however many attempts conflicted - but {len(unsettled)} were found. That "
            f"is the 'applied twice' failure Requirement 16.10 forbids. Case: {case!r}"
        )


# ══════════════════════════════════════════════════════════════════════════
# THE CENSUS - WHAT MAKES EACH RUN NON-VACUOUS
# ══════════════════════════════════════════════════════════════════════════

P23_FLOORS: Dict[str, int] = {
    "examples": EXAMPLES,
    # Two concurrent runs per example: once with the limit order outside and once with the market
    # order outside, so both version-guarded write sites are the one that gets overtaken.
    "concurrent_runs": EXAMPLES * 2,
    "outer_was_a_limit_order": EXAMPLES,
    "outer_was_a_market_order": EXAMPLES,
    # Forced by ``switch_at`` containing FORCED_SWITCH: the read immediately before the outer's
    # version-guarded UPDATE is always a switch point, so a conflict is arranged, not awaited.
    "a_real_conflict_occurred": EXAMPLES * 2,
    "the_outer_retried_and_then_succeeded": EXAMPLES * 2,
    # Forced by ``nested_switch_at`` containing FORCED_SWITCH, with a money-writer queued behind the
    # first release: the competitor is itself overtaken.
    "a_nested_submission_also_conflicted": EXAMPLES * 2,
    "competitors_released_inside_a_read": EXAMPLES * 2 * 2,
    "backoffs_slept": EXAMPLES * 2 * 2,
    "every_raced_intent_succeeded": EXAMPLES * 2,
    "a_rejected_intent_survived_the_race": EXAMPLES * 2,
    "a_resting_order_locked_funds": EXAMPLES * 2,
    "a_market_order_filled": EXAMPLES * 2,
    "a_position_was_opened": EXAMPLES * 2,
    "starved_409_left_a_durable_accepted_order": EXAMPLES * 2,
    "starved_limit_reported_partial_write": EXAMPLES,
    "starved_market_left_one_unsettled_fill": EXAMPLES,
    "sequential_permutations_agreed": EXAMPLES,
    "permutations_replayed": EXAMPLES * 24,
    # These four are DRAWN two-way choices, not forced cases, and the floors say so: a fair draw
    # over a hundred examples gives about fifty of each, and a fifth of that is low enough never to
    # be a distributional accident while still failing loudly if the generator ever stops drawing a
    # branch at all. Everything above this line is forced into every example and its floor is the
    # arithmetic consequence of that forcing.
    "side_buy": EXAMPLES // 5,
    "side_sell": EXAMPLES // 5,
    "limit2_was_a_second_limit_order": EXAMPLES // 5,
    "limit2_was_a_second_market_order": EXAMPLES // 5,
}

P23_LABELS: Dict[str, str] = {
    "outer_was_a_limit_order": "the overtaken submission was a resting limit order",
    "outer_was_a_market_order": "the overtaken submission was a market order",
    "a_real_conflict_occurred": "a version-guarded UPDATE genuinely matched no row",
    "the_outer_retried_and_then_succeeded": "a conflicted submission retried and completed",
    "a_nested_submission_also_conflicted": "the competitor was itself overtaken",
    "every_raced_intent_succeeded": "all four raced intents completed",
    "a_rejected_intent_survived_the_race": "a REJECTED order came through the race intact",
    "starved_limit_reported_partial_write": "an ORDER_LOCK exhaustion reported partial_write",
    "starved_market_left_one_unsettled_fill": "an apply_fill exhaustion left one unsettled fill",
    "sequential_permutations_agreed": "all 24 sequential permutations produced one result",
    "side_buy": "the raced intents were buys",
    "side_sell": "the raced intents were sells",
    "limit2_was_a_second_market_order": "the second money-writer was a market order",
}


def _assert_one_concurrent_run_matches_the_oracle(
    case: _ConfluenceCase,
    outer: str,
    starved: str,
    oracle: Dict[str, Any],
    keys: FrozenSet[str],
    recorder: Recorder,
) -> None:
    """P-23 for one concurrent run."""
    recorder.mark("concurrent_runs")
    recorder.mark(
        "outer_was_a_limit_order" if outer == "limit" else "outer_was_a_market_order"
    )
    race = _run_concurrently(case, outer, starved)
    supabase = race.supabase
    outcomes = race.outcomes
    context = f"outer={outer}, starved={starved}. Case: {case!r}"

    # ── the race was real, and real among the FOUR RACED intents rather than only in the
    #    starved submission, which conflicts by construction ──────────────────────────────
    slept = {name: race.sleeps[name].delays for name in SUCCEEDING_NAMES}
    for delays in list(slept.values()) + [race.starved_sleeps.delays]:
        for delay in delays:
            assert Decimal(str(delay)) in sim.RETRY_BACKOFF_SECONDS, (
                f"P-23: a slept delay of {delay} is not one of the recorded bounded backoffs "
                f"{sim.RETRY_BACKOFF_SECONDS}; a jittered backoff would break Requirement 15.4's "
                f"replay. {context}"
            )
    total_backoffs = sum(len(delays) for delays in slept.values())
    assert total_backoffs, (
        "P-23 would be vacuous for this run: none of the four raced submissions slept a retry "
        "backoff, so no version-guarded UPDATE among them ever matched no row and nothing was "
        f"concurrent. {context}"
    )
    recorder.mark("a_real_conflict_occurred")
    recorder.mark("backoffs_slept", total_backoffs)

    assert race.conflicted(outer), (
        "P-23: the OUTER submission never slept a backoff, so the forced switch point at its "
        f"second account read did not overtake it. Slept: {slept}. {context}"
    )
    recorder.mark("the_outer_retried_and_then_succeeded")
    competitors_that_conflicted = sorted(
        name for name in SUCCEEDING_NAMES if name != outer and race.conflicted(name)
    )
    assert competitors_that_conflicted, (
        "P-23: no COMPETITOR conflicted, so the forced nested switch point did not overtake the "
        f"submission that was released inside the outer's read and the deeper race went untested. "
        f"Slept: {slept}. {context}"
    )
    recorder.mark("a_nested_submission_also_conflicted")

    assert race.starved_sleeps.delays == [0.05, 0.10], (
        f"P-23 (Requirement 16.10): three attempts means two bounded waits, in that order; the "
        f"starved submission slept {race.starved_sleeps.delays}. {context}"
    )
    assert sorted(outcomes) == sorted(SUCCEEDING_NAMES), (
        f"P-23: the raced submissions recorded were {sorted(outcomes)}, not "
        f"{sorted(SUCCEEDING_NAMES)}; an intent was lost before it was even offered. {context}"
    )
    assert len(race.released) >= 2, (
        "P-23: fewer than two competitors were released INSIDE an account read, so the forced "
        f"depth-0 and depth-1 switch points did not both fire: released {race.released}. {context}"
    )
    recorder.mark("competitors_released_inside_a_read", len(race.released))

    # ── every raced intent succeeded, and each is present exactly once ────────────────────
    raced = {name: outcomes[name] for name in SUCCEEDING_NAMES}
    failed = sorted(name for name, record in raced.items() if not record.succeeded)
    assert failed == [], (
        f"P-23: {failed} answered 409 in the raced set. Requirement 16.10 permits that - three "
        f"conflicting attempts is a 409 - but this run queues at most two competitors behind a "
        f"three-attempt bound, so an exhaustion here means a competitor was released on an attempt "
        f"the schedule did not intend. {context}"
    )
    recorder.mark("every_raced_intent_succeeded")

    for name in SUCCEEDING_NAMES:
        key = case.key(name)
        matching = [
            row
            for row in supabase.orders
            if row.get("idempotency_key") is not None and str(row["idempotency_key"]) == key
        ]
        assert len(matching) == 1, (
            f"P-23: intent {name} has {len(matching)} order rows under key {key!r}. One would be "
            f"'applied once'; zero is 'lost' and two is 'applied twice', and Requirement 16.10 "
            f"forbids both. {context}"
        )
        returned = raced[name].outcome
        assert returned is not None and str(returned.order["id"]) == str(matching[0]["id"]), (
            f"P-23: intent {name} reported an order that is not the one persisted under its key. "
            f"{context}"
        )

    settled = _settled_fill_ids(supabase)
    fill_events = [
        (str(row["order_id"]), str(row["fill_event_id"]))
        for row in supabase.fills
        if str(row["id"]) in settled
    ]
    assert len(fill_events) == len(set(fill_events)), (
        f"P-23: a settled (order_id, fill_event_id) appears twice, so a fill was applied twice: "
        f"{fill_events}. {context}"
    )

    # ── the 409'd intent applied nothing, and its residue is exactly the documented one ───
    _assert_the_409_applied_nothing(supabase, case, starved, race.error)
    recorder.mark("starved_409_left_a_durable_accepted_order")
    if starved == "starved_limit":
        recorder.mark("starved_limit_reported_partial_write")
    else:
        recorder.mark("starved_market_left_one_unsettled_fill")

    # ── the whole point: the concurrent state IS a sequential state ───────────────────────
    _assert_the_equity_series_ends_where_the_account_stands(supabase, context)
    observed = _state(supabase, keys)
    differing = sorted(claim for claim in oracle if observed[claim] != oracle[claim])
    assert differing == [], (
        "P-23 (Requirements 16.10, 18.3): the concurrently applied intents did not land where "
        f"every sequential order of the same intents lands. Differing: {differing}. "
        + "; ".join(
            f"{claim}: sequential {oracle[claim]!r} against concurrent {observed[claim]!r}"
            for claim in differing
        )
        + f". {context}"
    )

    # ── and the shape of that state is not trivial ────────────────────────────────────────
    balances = observed["balances"]
    assert balances["locked_balance"] > 0, (
        f"P-23 would be vacuous: no funds are locked, so the resting limit order's version-guarded "
        f"lock never applied. {context}"
    )
    recorder.mark("a_resting_order_locked_funds")
    assert observed["settled_fills"], (
        f"P-23 would be vacuous: no fill settled, so no market order was applied. {context}"
    )
    recorder.mark("a_market_order_filled")
    assert observed["positions"], (
        f"P-23 would be vacuous: no position exists, so nothing was actually traded. {context}"
    )
    recorder.mark("a_position_was_opened")
    rejected_rows = [
        row
        for row in observed["orders"]
        if row[0] == case.key("rejected")
    ]
    assert len(rejected_rows) == 1 and rejected_rows[0][1] == PaperOrderState.REJECTED.value, (
        f"P-23: the rejected intent did not come through the race as a persisted REJECTED order: "
        f"{rejected_rows}. {context}"
    )
    assert rejected_rows[0][7] == case.rejected_intent[0], (
        f"P-23: the rejected intent's recorded reason is {rejected_rows[0][7]!r}, not the declared "
        f"{case.rejected_intent[0]!r}. {context}"
    )
    recorder.mark("a_rejected_intent_survived_the_race")

    assert Decimal(str(supabase.accounts[0]["version"])) > 1, (
        f"P-23: the account's version never moved, so no optimistic-concurrency write happened at "
        f"all. {context}"
    )


def test_p23_concurrent_intents_match_a_sequential_order(request: Any) -> None:
    """Concurrently submitted distinct intents land where a sequential order of them lands.

    For all generated sets of concurrently submitted **distinct** intents against one
    Paper_Account: the resulting balances, positions and equity - compared as exact ``Decimal`` -
    are identical to those produced by applying the same intents in some sequential order, no
    intent is applied twice (exactly one order row per idempotency key, exactly one settled fill per
    fill event), and no intent is lost.

    An intent that answered 409 ``PAPER_CONCURRENCY_CONFLICT`` was **not** applied: it moved no
    balance, opened no position and added no equity point, and the residue this transport does leave
    is asserted exactly - see the module docstring's "THE 409 RESIDUE". The property deliberately
    does not claim that every concurrently submitted intent succeeds: over PostgREST there is no
    ``SELECT ... FOR UPDATE`` and no ``ROLLBACK``, so a concurrent submission resolves either as a
    serialised success or as a 409 after Requirement 16.10's three attempts.

    The oracle is every sequential permutation of the intents that succeeded - all twenty-four of
    them, replayed one intent at a time on twenty-four fresh stores with nothing racing - asserted
    to produce one common result. Concurrency is produced through the Persistence_Layer double's
    ``after_select`` seam, which runs a real competing ``submit_intent`` inside the account read that
    immediately precedes a version-guarded UPDATE; the schedule forces that read to be a switch
    point, so a genuine conflict occurs on every example and the census fails the run if one did
    not.

    **Validates: Requirements 16.10, 18.3**
    """
    recorder = Recorder("P-23", P23_FLOORS, P23_LABELS)

    @PROPERTY_SETTINGS
    @given(case=confluent_intent_sets())
    def check(case: _ConfluenceCase) -> None:
        recorder.start()
        recorder.mark("examples")
        recorder.mark(f"side_{case.side}")
        recorder.mark(
            "limit2_was_a_second_limit_order"
            if case.limit2_order_type == "limit"
            else "limit2_was_a_second_market_order"
        )

        keys = frozenset(case.key(name) for name in SUCCEEDING_NAMES)
        oracle = _sequential_oracle(case, SUCCEEDING_NAMES, keys)
        recorder.mark("sequential_permutations_agreed")
        recorder.mark("permutations_replayed", 24)

        _assert_one_concurrent_run_matches_the_oracle(
            case, "limit", "starved_limit", oracle, keys, recorder
        )
        _assert_one_concurrent_run_matches_the_oracle(
            case, "market", "starved_market", oracle, keys, recorder
        )
        recorder.finish()

    try:
        with publish_hypothesis_statistics(request.node):
            check()
    finally:
        repo.reset_persistence_probe()

    recorder.assert_not_vacuous()


# ══════════════════════════════════════════════════════════════════════════
# THE LITERALS THIS FILE WROTE DOWN, HELD AGAINST THEIR SOURCES
# ══════════════════════════════════════════════════════════════════════════


def test_the_written_down_retry_bound_is_the_modules_own() -> None:
    """Requirement 16.10's "at most 3 times", and the jitter-free backoff a replay needs."""
    assert sim.RETRY_ATTEMPTS == REQUIREMENT_16_10_ATTEMPTS
    assert sim.RETRY_BACKOFF_SECONDS == (Decimal("0.05"), Decimal("0.10"))


def test_the_declared_rejection_reasons_are_the_modules_own() -> None:
    """The four ``REJECTED_INTENTS`` reasons are spelled the way ``paper_simulator`` spells them."""
    declared = {reason for reason, _ in REJECTED_INTENTS}
    assert declared <= set(sim.REJECTION_REASONS), (
        f"P-23 declares rejection reasons the module does not know: "
        f"{sorted(declared - set(sim.REJECTION_REASONS))}"
    )


def test_the_transport_still_has_no_transaction_to_roll_back() -> None:
    """The premise of this whole file, pinned so a change to it is a change to this property.

    If ``paper_repository`` ever gains a real ``SELECT ... FOR UPDATE`` or a transaction wrapper,
    P-23's honest statement changes: the 409 residue this file asserts EXACTLY would no longer be
    reachable, and the property should be tightened to "every concurrent intent either succeeds or
    leaves nothing at all". Until then, this is what the transport offers.
    """
    import inspect

    # No transaction control exists to be called.
    for name in ("begin", "commit", "rollback", "transaction", "in_transaction"):
        assert not hasattr(repo, name), (
            f"paper_repository now exposes {name!r}; P-23's premise - that there is no transaction "
            "to roll back and the 409 residue below is therefore reachable - has changed"
        )

    # The substitute for the row lock is still the optimistic version predicate, and it is still
    # what a lost update surfaces as.
    assert ".eq(\"version\"" in inspect.getsource(repo.bump_version), (
        "bump_version no longer carries the version predicate; the conflict this property arranges "
        "would no longer be detectable"
    )
    assert issubclass(repo.PaperConcurrencyConflict, Exception)
    conflict = repo.PaperConcurrencyConflict(
        "the paper account update matched no row", table=repo.ACCOUNTS_TABLE
    )
    assert sim._is_retryable(conflict), (
        "a version conflict is no longer retryable, so Requirement 16.10's three attempts do not "
        "apply and this property's arrangement is meaningless"
    )


def test_exactly_one_property_function_is_claimed_by_this_module() -> None:
    """The scoreboard counts nested functions too, so a stray ``test_p23_`` would claim P-23 twice."""
    import ast
    import inspect
    import re
    from pathlib import Path

    path = Path(inspect.getsourcefile(test_p23_concurrent_intents_match_a_sequential_order) or "")
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    pattern = re.compile(r"^test_p(\d+)_")
    claimed = sorted(
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and pattern.match(node.name)
    )
    assert claimed == ["test_p23_concurrent_intents_match_a_sequential_order"], (
        f"this module claims {claimed}"
    )
    top_level = sorted(
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and pattern.match(node.name)
    )
    assert top_level == claimed, "the property claim is nested rather than at module scope"


def test_this_module_never_creates_its_own_event_loop() -> None:
    """Every depth-0 coroutine goes through the harness's ``_run_coroutine``.

    The one exception is deliberate and cannot be otherwise: :func:`_drive_without_a_loop` steps the
    NESTED submission by hand, because a loop is already running when the double's seam fires and
    ``run_until_complete`` refuses to start a second one inside it. That function is not a second
    loop - it creates none - and it fails loudly rather than silently if the coroutine ever
    suspends.

    Asserted on the parsed CALLS rather than on the source text, so the check does not trip over the
    names it is looking for inside its own assertions.
    """
    import ast
    import inspect
    from pathlib import Path

    path = Path(inspect.getsourcefile(_run_sequentially) or "")
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    called = {
        ast.unparse(node.func)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, (ast.Attribute, ast.Name))
    }
    forbidden = sorted(
        name
        for name in called
        if name in ("asyncio.run", "asyncio.new_event_loop", "asyncio.set_event_loop")
        or name.endswith(("new_event_loop", "run_until_complete"))
    )
    assert forbidden == [], f"this module builds its own event loop: {forbidden}"
    assert callable(_run_coroutine), "the harness's loop runner is not importable"
    assert "asyncio" not in {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }, "this module imports asyncio directly rather than using the harness's runner"
