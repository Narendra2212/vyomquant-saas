"""
tests/property/test_absent_vs_zero.py - production-launch-hardening task 2, CLUSTER A.

Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.36. ``design.md`` §Hypothesized Root Cause (wave 1).

WHAT THIS FILE IS
-----------------
**A preservation file.** Every hard assertion here PASSES on the unfixed tree (``F``), and the
whole point of it existing before wave 1 is that it must still pass on ``F'``. It is the opposite
number of ``tests/test_dashboard_absent_figures.py`` (committed at ``f0b1b53``), which is the
exploration file: there every assertion is expected to FAIL, and the failures are the
counterexamples that prove the defect. Nothing is duplicated between them - that file states what
must change, this file states what must not.

THE PROPERTY, IN BOTH DIRECTIONS
--------------------------------
Over generated account rows, QuestDB responses and equity series:

1. **A read that SUCCEEDED reports its value unchanged** - including ``0.0``, ``-0.0``, a
   genuinely empty ledger, and a real balance that happens to equal the paper starting capital.
   A zero is a measurement.
2. **Only a read that FAILED reports absent.**

Direction 1 alone is satisfied by today's tree, which fabricates. Direction 2 alone is satisfied
by a "fix" that nulls every money figure unconditionally - which is precisely wave 1's own failure
mode, and the reason this file would be worthless one-directional. So every property below carries
both limbs, and where the two limbs cannot be pinned against the same subject the file says which
subject each is pinned against rather than weakening one to reach the other.

WHY THE TWO DIRECTIONS ARE PINNED AGAINST DIFFERENT SUBJECTS
------------------------------------------------------------
A measured constraint from task 1, recorded in that file's section 5 and re-verified here:
``dashboard_aggregation_service.py:1798-1815`` coerces every money figure with ``float(...)``, so
a ``None`` raises ``TypeError``; and ``get_dashboard_data``'s outer ``except`` **re-raises** at
``:1889-1890``, so a ``None`` money figure 500s the whole dashboard today. Direction 2 therefore
cannot be asserted against the composer on ``F`` without the file failing, and a file that fails
cannot pin anything.

The resolution is not to weaken direction 1 and not to fake a pass. Each direction is pinned
against a subject where it is honestly checkable **today**:

=======================================  ==========================================  ============
Subject                                  Direction pinned                            Holds on ``F``
=======================================  ==========================================  ============
``_finite_float`` (``:314``)             1 **and** 2, exactly - it is an ``iff``     yes, hard
``get_portfolio_overview`` paper row     1, over generated rows                      yes, hard
``get_portfolio_overview`` ``realized_pnl``  1 **and** 2 - BC-5 already honest       yes, hard
``get_equity_curve`` with rows           1, over generated QuestDB responses         yes, hard
``get_equity_curve`` live, no rows       2 - the live limb already reports ``[]``    yes, hard
``get_dashboard_data`` overview          1, over generated successful portfolios     yes, hard
``get_equity_curve`` paper, no rows      2                                          no - xfail
``get_dashboard_data`` with ``None``     2                                          no - xfail
``get_portfolio_overview`` bad fill      2                                          no - xfail
=======================================  ==========================================  ============

``_finite_float`` (``:314``) is the load-bearing one. It is this codebase's own absent-value reader
- ``Any -> Optional[float]``, ``None`` for a non-number, a NaN or an infinity, and booleans are not
money - and it is what wave 1 will apply at every one of the four fabrication sites. Property-tested
here in both directions as a biconditional: ``_finite_float(x) is None`` **if and only if** ``x``
is not a finite number. That is the whole of the absent-vs-zero rule, enforced on the exact function
that will carry it, with no fabrication site in the way.

The three ``xfail``s are the fixed-state expectations that the ``float()`` gate refuses today. They
are marked **non-strict on purpose**: this is a preservation file, so it must pass on ``F`` *and* on
``F'``, and a strict marker would turn wave 1's success into a red run in the one file whose job is
to stay green across the fix. The counterexamples those three describe are already asserted
strictly, as expected failures, in ``tests/test_dashboard_absent_figures.py`` sections 2-4; that is
where the "this must change" claim lives. Here they document the target and are exercised - the
property body runs on every example either way, so a NEW way of going wrong (a different exception,
a hang, a fabricated figure in a field direction 1 does not cover) still shows up in the report.

WHAT IS NOT DOUBLED
-------------------
The doubles are ``tests/test_dashboard_absent_figures.py``'s, imported rather than rebuilt:
``_EmptyQuestDB``, ``_paper_account_row``, ``_no_cached_balances``, ``_paper_service_raising``,
``_portfolio_overview_returning``, ``_genuine_zero_portfolio``, ``_fabricated_capital_sites``,
``requires_routers`` and the key tuples. Two harnesses for one service would be two accounts of what
a dashboard read is. The two QuestDB variants below (``_QuestDBWithRows``, ``_QuestDBUnreachable``)
are **subclasses** of ``_EmptyQuestDB`` that change only what the query answers with, so the
recording and the interface stay that file's.

The real ``DashboardAggregationService`` is driven throughout. Nothing in this file mocks the
service, and no oracle is computed by calling the code under test: direction 1's oracle is the
generated input itself, carried alongside the response and compared field by field.

EXACTNESS
---------
No ``pytest.approx``, no ``round()``, no tolerance. Money figures are compared with
:func:`_identical_float`, which is ``==`` **plus** a sign check, because ``-0.0 == 0.0`` is true in
Python and a fix that flipped the sign of a zero balance would otherwise pass. The sign claim is
made only about the figures the service reads and carries verbatim; a derived sum
(``today_pnl = today_realized_pnl + unrealized_pnl``) is compared by value alone, since
``0.0 + -0.0`` is ``0.0`` by IEEE 754 and not by anything this spec has an opinion about.

NON-VACUITY
-----------
A generated money figure of ``317.5`` exercises neither zero, so every generator **forces** its hard
cases into every example: ``0.0``, ``-0.0``, and ``100000.0`` as a genuine measured balance are
sampled members of the money strategy, and each property carries a :class:`Recorder`
(``tests/property/paper_census.py``) with an explicit floor and a Hypothesis ``event`` label per
bucket. What is counted is what the run OBSERVED. A shortfall is fixed by forcing the case in the
generator, never by lowering a floor.

A STATED GAP
------------
``today_return_pct`` is not pinned by direction 1. It is
``round(today_pnl / initial_capital * 100, 2)``, so the only oracle for it is that same expression,
and a test that recomputes the implementation asserts nothing. Its absent-state behaviour is pinned
instead - by ``test_dashboard_absent_figures.py`` section 4, whose ``initial_capital`` case is the
one where the fabrication is a divisor and never appears in the body.
"""

from __future__ import annotations

import asyncio
import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from backend_app.backend.dashboard_aggregation_service import (
    DashboardAggregationService,
    _finite_float,
)

# ── The census, written once for the property modules that need it. ───────────────────────
from tests.property.paper_census import Recorder, publish_hypothesis_statistics

# ── The doubles. tests/test_dashboard_absent_figures.py's, imported rather than rebuilt. ──
from tests.test_dashboard_absent_figures import (
    COMPLETE_PAPER_ROW,
    FABRICATED_CAPITAL,
    OVERVIEW_MONEY_KEYS,
    _EmptyQuestDB,
    _fabricated_capital_sites,
    _genuine_zero_portfolio,
    _no_cached_balances,
    _paper_account_row,
    _paper_service_raising,
    _portfolio_overview_returning,
    requires_routers,
)
from unittest.mock import patch

#: ``design.md § Property-based testing configuration``: at least 100 examples, no per-example
#: deadline.
EXAMPLES = 100

PROPERTY_SETTINGS = settings(
    max_examples=EXAMPLES,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)

#: ``get_dashboard_data`` fans out over six reads per call (``asyncio.gather`` at ``:1650``), so the
#: composer properties run a smaller census. Not a concession about coverage: the composer's money
#: block is a single dict literal at ``:1798-1815`` with one ``float()`` per key, so what varies
#: between examples is the VALUE, and the forced spine puts ``0.0``, ``-0.0`` and the starting
#: capital into every example regardless of the count.
COMPOSER_EXAMPLES = 25

COMPOSER_SETTINGS = settings(
    max_examples=COMPOSER_EXAMPLES,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)

USER: Dict[str, Any] = {
    "id": "test_user_absent_vs_zero",
    "email": "trader@vyomquant.com",
    "access_token": "valid_jwt_token",
}


# ══════════════════════════════════════════════════════════════════════════
# THE FORCED SPINE - THE VALUES THAT MAKE "A ZERO IS A MEASUREMENT" A CLAIM
# ══════════════════════════════════════════════════════════════════════════

#: Money figures forced into the strategy on every draw.
#:
#: * ``0.0`` - a funded account withdrawn to zero. The case the whole spec turns on.
#: * ``-0.0`` - the same balance carrying IEEE 754's other zero. A round-trip through a JSON
#:   encoder, a Postgres ``numeric`` or a ``sum()`` can produce it, and it must not become ``None``
#:   on the way out any more than ``0.0`` must.
#: * ``100000.0`` - the paper STARTING capital as a genuine, measured, persisted balance. This is
#:   the adversarial one: a fix that scrubbed the fabricated literal by matching on its value would
#:   erase a real account that happens to hold exactly the figure it opened with. Absence is a fact
#:   about the READ, never about the number.
#: * ``1e-9`` / ``-1e-9`` - either side of zero at a magnitude a truthiness test would swallow, so
#:   a fix written as ``value or None`` is caught.
#:
#: These are FORCED, never sampled and hoped for. The properties below place every member of
#: :data:`FORCED_SPINE` into a money slot on **every** example - by iterating the tuple, or by
#: drawing a permutation that arranges the spine across the slots - so the census floors are
#: statements about the spine working rather than about how often a draw happened to land.
FORCED_MONEY: Tuple[float, ...] = (0.0, -0.0, FABRICATED_CAPITAL, 1e-9, -1e-9, 4200.0)

#: The three that must appear in every example of the service-level properties. Exactly three, so
#: that with one freely generated figure the spine fills a four-slot money block exactly - see
#: :func:`_arrange`.
FORCED_SPINE: Tuple[float, ...] = (0.0, -0.0, FABRICATED_CAPITAL)

#: The four zeros and near-zeros that any "is this absent?" shortcut gets wrong.
ZERO_LIKE: Tuple[float, ...] = (0.0, -0.0, 1e-9, -1e-9)


def money() -> st.SearchStrategy[float]:
    """A money figure that was genuinely read. Finite, and the hard cases are always in range."""
    return st.one_of(
        st.sampled_from(FORCED_MONEY),
        st.floats(
            min_value=-1e9,
            max_value=1e9,
            allow_nan=False,
            allow_infinity=False,
            width=64,
        ),
    )


def arrangement() -> st.SearchStrategy[List[int]]:
    """A permutation of the four money slots, for :func:`_arrange`."""
    return st.permutations([0, 1, 2, 3])


def _arrange(order: List[int], free: float) -> Tuple[float, float, float, float]:
    """:data:`FORCED_SPINE` plus one freely generated figure, spread over four slots by ``order``.

    This is what makes the census floors below equal the example count rather than a guess at a
    sampling frequency. ``0.0``, ``-0.0`` and a genuine ``100000.0`` are in **every** example, and
    ``order`` decides which figure each one lands on - so no single slot is privileged and a fix
    that special-cased one field is still caught.
    """
    pool = list(FORCED_SPINE) + [free]
    a, b, c, d = (pool[index] for index in order)
    return a, b, c, d


def window() -> st.SearchStrategy[int]:
    """A requested equity window in days, as ``get_equity_curve`` takes it.

    Generated rather than fixed because the paper synthesis at ``:1338-1344`` HONOURS it - the
    fabricated pair spans exactly the window the caller asked for, so a 90-day request renders as
    ninety days of measured break-even performance. The sampled members are the windows the
    dashboard actually requests; the integer range is what keeps the input space large enough that
    the census below is not exhausted after a handful of examples.
    """
    return st.one_of(
        st.sampled_from((1, 7, 30, 90, 365)),
        st.integers(min_value=1, max_value=365),
    )


def positive_money() -> st.SearchStrategy[float]:
    """A capital base. Strictly positive, so ``initial_capital > 0`` at ``:934`` is satisfied."""
    return st.one_of(
        st.sampled_from((1e-9, 1.0, 4000.0, FABRICATED_CAPITAL)),
        st.floats(
            min_value=1e-6,
            max_value=1e9,
            allow_nan=False,
            allow_infinity=False,
            width=64,
        ),
    )


#: Values that are NOT a number, for :func:`test_finite_float_is_absent_exactly_when_unreadable`.
#: Each one is a way a figure genuinely arrives unreadable from a real producer:
#: a SQL ``NULL``, a flag column typed as money, a text column, a ``0/0``, an overflowed sum.
NOT_A_NUMBER: Tuple[Any, ...] = (
    None,
    True,
    False,
    "",
    "   ",
    "abc",
    "nan",
    "inf",
    "-inf",
    "NaN",
    float("nan"),
    float("inf"),
    float("-inf"),
    Decimal("NaN"),
    Decimal("Infinity"),
    [],
    {},
    (),
    object(),
)

#: Finite numbers that are not ``float``. A real read hands back whichever of these its driver uses
#: - ``Decimal`` from Postgres ``numeric``, ``int`` from a minor-unit column, ``str`` from QuestDB's
#: JSON. All three must be reported as the number they denote.
NUMERIC_NOT_FLOAT: Tuple[Any, ...] = (
    0,
    -0,
    1,
    -1,
    100000,
    Decimal("0"),
    Decimal("-0.0"),
    Decimal("0.00"),
    Decimal("4200.5"),
    Decimal("100000"),
    "0",
    "-0.0",
    "0.0",
    "4200.5",
    "100000.0",
    "1e-9",
)


# ══════════════════════════════════════════════════════════════════════════
# EXACT COMPARISON, INCLUDING THE SIGN OF A ZERO
# ══════════════════════════════════════════════════════════════════════════


def _identical_float(observed: Any, expected: float) -> bool:
    """``observed`` is ``expected`` as a float, sign of zero included.

    ``-0.0 == 0.0`` is true in Python, so ``==`` alone cannot see a flipped zero. A balance of
    ``-0.0`` reported as ``0.0`` is a changed measurement, and this file's whole subject is
    measurements surviving unchanged, so the sign is part of the comparison.
    """
    if observed is None or isinstance(observed, bool):
        return False
    if not isinstance(observed, (int, float)):
        return False
    value = float(observed)
    if value != expected:
        return False
    return math.copysign(1.0, value) == math.copysign(1.0, expected)


def _describe(observed: Any, expected: float) -> str:
    """``observed`` and ``expected`` spelled so a ``-0.0`` mismatch is legible in the failure."""
    return f"observed {observed!r} (sign {_sign(observed)}), expected {expected!r} (sign {_sign(expected)})"


def _sign(value: Any) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return "-" if math.copysign(1.0, float(value)) < 0 else "+"
    return "?"


def _run_coroutine(coro: Any) -> Any:
    """Run ``coro`` to completion **without leaving the thread without an event loop**.

    The convention every async-driving test in this repository uses
    (``tests/test_paper_market_feed_selection.py``, ``tests/test_expiry_sweep.py`` and nine others).
    ``asyncio.run`` is not used: it closes the loop it created, which breaks any later test in the
    session that expected one. ``pytest.mark.asyncio`` is not used either, because Hypothesis drives
    each property many times inside one test function and the loop lifetime has to be per call.
    """
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
# THE TWO QUESTDB ANSWERS `_EmptyQuestDB` DOES NOT COVER
# ══════════════════════════════════════════════════════════════════════════


class _ReadFailed(Exception):
    """A telemetry outage, spelled as its own type so a test cannot mistake it for a test bug."""


class _QuestDBWithRows(_EmptyQuestDB):
    """``_EmptyQuestDB`` that answers with a dataset: a successful read that FOUND something.

    Subclassed rather than written fresh - the query recording and the interface are the imported
    double's, and only the answer differs. ``_EmptyQuestDB`` is the empty-but-successful read;
    this is the non-empty one; :class:`_QuestDBUnreachable` is the failed one. Three answers, one
    double.
    """

    def __init__(self, response: Dict[str, Any]) -> None:
        super().__init__()
        self._response = response

    async def execute_query(self, query: str):
        self.queries.append(query)
        return self._response


class _QuestDBUnreachable(_EmptyQuestDB):
    """``_EmptyQuestDB`` that raises: the read genuinely FAILED, which is direction 2's premise."""

    async def execute_query(self, query: str):
        self.queries.append(query)
        raise _ReadFailed("questdb unreachable")


def _equity_response(equities: List[float], *, days: int) -> Dict[str, Any]:
    """A QuestDB ``equity_curve`` response carrying ``equities``, oldest first.

    Shaped exactly as ``get_equity_curve`` reads it at ``:1332-1334`` -
    ``[c["name"] for c in result["columns"]]`` zipped against each ``dataset`` row - so the
    timestamps and equities below travel the same path a real QuestDB answer travels.
    """
    start = datetime.now(timezone.utc) - timedelta(days=days)
    return {
        "columns": [{"name": "timestamp"}, {"name": "equity"}],
        "dataset": [
            [(start + timedelta(minutes=15 * index)).isoformat(), equity]
            for index, equity in enumerate(equities)
        ],
    }


def _paper_row(
    *,
    total_equity: float,
    available_balance: float,
    locked_balance: float,
    unrealized_pnl: float,
    realized_pnl: float,
    initial_capital: float,
) -> Dict[str, Any]:
    """A COMPLETE paper account row - every column present, every value a real persisted figure.

    Built off the imported :data:`COMPLETE_PAPER_ROW` so the shape is that file's and only the money
    varies. Completeness is the premise of direction 1: the read succeeded and found everything, so
    everything must be reported. ``test_dashboard_absent_figures.py`` section 4 owns the other case,
    where a column is genuinely missing.
    """
    row = dict(COMPLETE_PAPER_ROW)
    row.update(
        {
            "total_equity": total_equity,
            "available_balance": available_balance,
            "locked_balance": locked_balance,
            "unrealized_pnl": unrealized_pnl,
            "realized_pnl": realized_pnl,
            "initial_capital": initial_capital,
        }
    )
    return row


def _paper_service_with(row: Dict[str, Any], trades: List[Dict[str, Any]]):
    """``_paper_account_row``'s stub, with a fill ledger.

    ``_paper_account_row`` hard-codes an empty ledger, which is one of the cases direction 1 has to
    cover (an empty ledger read successfully is ``0.0``, not absent) but not all of them: the
    lifetime realised figure at ``:920-926`` is a sum over the LEDGER, so pinning it needs rows.
    Same patch target, same two-method surface.
    """

    class _StubPaperService:
        def get_or_create_account(self, user_id):
            return dict(row)

        def get_trades(self, user_id):
            return [dict(trade) for trade in trades]

    return patch(
        "backend_app.backend.paper_trading_service.get_paper_trading_service",
        return_value=_StubPaperService(),
    )


#: An instant safely before today's 00:00 UTC cutoff, so a fill dated here is excluded from
#: ``today_realized_pnl`` (``:911-915``) and included in the lifetime sum (``:920-926``).
YESTERDAY = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()

#: An instant safely after it.
TODAY = datetime.now(timezone.utc).isoformat()


def _fill(realized_pnl: Any, *, executed_at: str) -> Dict[str, Any]:
    """One paper fill, carrying whatever ``realized_pnl`` the generator drew - readable or not."""
    return {"realized_pnl": realized_pnl, "executed_at": executed_at, "symbol": "BTC/USDT"}


# ══════════════════════════════════════════════════════════════════════════
# 1. `_finite_float` (:314) - BOTH DIRECTIONS, AS A BICONDITIONAL
# ══════════════════════════════════════════════════════════════════════════
#
# The honest half already present in the service, and what wave 1 will apply at all four
# fabrication sites. Pinned here with no fabrication site in the way, so the rule itself is under
# test rather than one site's application of it.


def test_finite_float_is_absent_exactly_when_unreadable(request):
    """``_finite_float(x) is None`` **if and only if** ``x`` is not a finite number.

    Both directions in one property, because they are one property. Stated as a biconditional and
    not as two implications, so neither of the two degenerate readers can pass it:

    * a reader that returns ``None`` for everything fails the forward limb on ``0.0``;
    * a reader that returns ``0.0`` for everything fails the reverse limb on ``None``.

    The forward limb is exact including the sign of a zero. The reverse limb covers the five ways a
    figure arrives unreadable from a real producer - a SQL ``NULL``, a boolean column, a text
    column, a ``0/0`` NaN, an overflowed infinity - plus ``Decimal('NaN')``, which ``float()``
    accepts happily and which a naive ``try: float(x)`` reader would publish as ``nan``.

    ``True`` is in the unreadable set deliberately and is not an edge case: ``float(True)`` is
    ``1.0``, so a reader without the ``isinstance(raw, bool)`` guard publishes a flag column as one
    dollar. Requirement 1.1 is about figures a trader acts on, and a boolean is not one.
    """
    census = Recorder(
        "finite_float biconditional",
        floors={
            # The generated figure, once per example, plus every member of FORCED_MONEY.
            "readable_float": EXAMPLES * (1 + len(FORCED_MONEY)),
            # 0.0 and -0.0 are both in FORCED_MONEY, so two marks per example at minimum.
            "readable_zero": EXAMPLES * 2,
            "readable_negative_zero": EXAMPLES,
            "readable_starting_capital": EXAMPLES,
            "readable_non_float": EXAMPLES,
            "unreadable": EXAMPLES,
        },
        labels={
            "readable_float": "a finite float was reported unchanged",
            "readable_zero": "a genuine 0.0 survived",
            "readable_negative_zero": "a genuine -0.0 survived with its sign",
            "readable_starting_capital": "a genuine 100000.0 survived",
            "readable_non_float": "an int/Decimal/str figure was reported as its number",
            "unreadable": "a non-number was reported absent",
        },
    )

    @given(
        readable=money(),
        non_float=st.sampled_from(NUMERIC_NOT_FLOAT),
        unreadable=st.sampled_from(NOT_A_NUMBER),
    )
    @PROPERTY_SETTINGS
    def check(readable: float, non_float: Any, unreadable: Any) -> None:
        census.start()

        # ── Direction 1, the strong form: the input IS a float, so the output must be that
        #    same float bit for bit. No conversion stands between them to excuse a difference.
        #
        #    The whole forced spine is checked on every example alongside the generated figure -
        #    this reader is a pure function of one argument, so there is nothing to be gained by
        #    spreading the hard cases across examples and a floor that depends on how often a
        #    draw landed on 0.0 is not a claim about anything.
        for figure in (readable,) + FORCED_MONEY:
            observed = _finite_float(figure)
            assert _identical_float(observed, figure), (
                f"_finite_float changed a finite float it was handed: "
                f"{_describe(observed, figure)}"
            )
            census.mark("readable_float")
            if figure == 0.0:
                census.mark("readable_zero")
                if math.copysign(1.0, figure) < 0:
                    census.mark("readable_negative_zero")
            if figure == FABRICATED_CAPITAL:
                census.mark("readable_starting_capital")

        # ── Direction 1 for the numbers a driver hands back as something other than a float.
        observed_non_float = _finite_float(non_float)
        assert observed_non_float is not None, (
            f"{non_float!r} is a finite number, so it was read; _finite_float reported it absent"
        )
        assert _identical_float(observed_non_float, float(non_float)), (
            f"_finite_float({non_float!r}) is not the number it denotes: "
            f"{_describe(observed_non_float, float(non_float))}"
        )
        census.mark("readable_non_float")

        # ── Direction 2: absent is reported for exactly the values that are not a number.
        assert _finite_float(unreadable) is None, (
            f"{unreadable!r} is not a finite number, so there is no figure here to report; "
            f"_finite_float returned {_finite_float(unreadable)!r}"
        )
        census.mark("unreadable")

        census.finish()

    with publish_hypothesis_statistics(request.node):
        check()
    census.assert_not_vacuous()


def test_finite_float_never_confuses_a_zero_with_an_absence(request):
    """The biconditional, restricted to the collision the whole spec is about.

    Direction 1 and direction 2 side by side on the SAME call, one example: a zero-like figure and
    an unreadable one, and the two answers must differ. A single assertion that no implementation
    collapsing the two can satisfy, however it is written - by nulling, by zeroing, by truthiness.

    ``1e-9`` and ``-1e-9`` are in :data:`ZERO_LIKE` because ``value or None`` and ``if not value``
    swallow ``0.0`` but not ``1e-9``, so a fix written either way is caught by the pair rather than
    only by the zero.
    """
    census = Recorder(
        "zero is not absence",
        floors={"pair": EXAMPLES // 2, "exact_zero": EXAMPLES // 8},
        labels={
            "pair": "a zero-like figure and an unreadable one were told apart",
            "exact_zero": "the zero-like figure was exactly 0.0 or -0.0",
        },
    )

    @given(
        zero_like=st.sampled_from(ZERO_LIKE),
        unreadable=st.sampled_from(NOT_A_NUMBER),
    )
    @PROPERTY_SETTINGS
    def check(zero_like: float, unreadable: Any) -> None:
        census.start()

        read = _finite_float(zero_like)
        not_read = _finite_float(unreadable)

        assert read is not None, (
            f"{zero_like!r} was read and is a number, so it is present; got None"
        )
        assert _identical_float(read, zero_like), _describe(read, zero_like)
        assert not_read is None, (
            f"{unreadable!r} is not a number, so it was not read; got {not_read!r}"
        )
        assert (read is None) != (not_read is None), (
            "a measured zero and an unreadable figure produced the same answer; a trader cannot "
            "tell an empty account from a broken read"
        )
        census.mark("pair")
        if zero_like == 0.0:
            census.mark("exact_zero")

        census.finish()

    with publish_hypothesis_statistics(request.node):
        check()
    census.assert_not_vacuous()


# ══════════════════════════════════════════════════════════════════════════
# 2. `get_portfolio_overview`, PAPER - DIRECTION 1 OVER GENERATED ACCOUNT ROWS
# ══════════════════════════════════════════════════════════════════════════
#
# The real service, the real paper branch (:891-949). Every figure the row carries must come back
# out unchanged. This is what wave 1 must not break, and it is where a fix that nulls everything
# dies.


def test_a_paper_row_that_was_read_reports_every_figure_unchanged(request):
    """Direction 1 against the real service: a complete row is reported exactly as read.

    Four directly-read figures are pinned with the sign of their zero intact, because the service
    carries them verbatim - ``total_value`` is ``total_equity`` (``:939``), ``free_balance`` is
    ``available_balance`` (``:899``), ``used_balance`` is ``locked_balance``. Nothing here is
    derived, so nothing here has an excuse to differ from the row.

    ``unrealized_pnl`` and ``cumulative_pnl`` are pinned by value only: the second is
    ``realized_pnl + unrealized_pnl``, and IEEE 754 makes ``0.0 + -0.0`` equal ``0.0``, which is
    arithmetic rather than a lost measurement.

    The generated row includes ``100000.0`` as a real balance on a sampled fraction of examples.
    That is the adversarial case for wave 1: a fix that scrubbed the fabricated literal by matching
    on its VALUE would erase the account of a trader who has not yet traded, and this property
    fails if it does. Absence is a fact about the read.
    """
    census = Recorder(
        "paper row round-trip",
        floors={
            "row": COMPOSER_EXAMPLES,
            # The spine places 0.0 and -0.0 into two of the four verbatim slots on every example,
            # and `total_value`/`free_balance` are carried copies of two of them - so the zero
            # buckets are floored at the example count, not at a sampling frequency.
            "zero_figure": COMPOSER_EXAMPLES * 2,
            "negative_zero_figure": COMPOSER_EXAMPLES,
            "genuine_starting_capital": COMPOSER_EXAMPLES,
            "empty_ledger": COMPOSER_EXAMPLES // 5,
        },
        labels={
            "row": "a complete paper row was reported unchanged",
            "zero_figure": "a genuine 0.0 balance survived",
            "negative_zero_figure": "a genuine -0.0 balance survived with its sign",
            "genuine_starting_capital": "a genuine 100000.0 balance survived",
            "empty_ledger": "a genuinely empty ledger reported 0.0, not absent",
        },
    )

    service = DashboardAggregationService()

    @given(
        order=arrangement(),
        free=money(),
        realized_pnl=money(),
        initial_capital=positive_money(),
        # `just([])` as its own branch rather than `min_size=0`: an empty ledger read successfully
        # is one of the two cases this property exists for, so it is forced onto roughly half the
        # examples instead of being left to how often a list draw comes up empty.
        ledger=st.one_of(
            st.just([]),
            st.lists(
                st.tuples(money(), st.sampled_from((YESTERDAY, TODAY))),
                min_size=1,
                max_size=4,
            ),
        ),
    )
    @COMPOSER_SETTINGS
    def check(
        order: List[int],
        free: float,
        realized_pnl: float,
        initial_capital: float,
        ledger: List[Tuple[float, str]],
    ) -> None:
        census.start()

        # The four figures the service carries verbatim, carrying the forced spine.
        total_equity, available_balance, locked_balance, unrealized_pnl = _arrange(order, free)

        row = _paper_row(
            total_equity=total_equity,
            available_balance=available_balance,
            locked_balance=locked_balance,
            unrealized_pnl=unrealized_pnl,
            realized_pnl=realized_pnl,
            initial_capital=initial_capital,
        )
        trades = [_fill(pnl, executed_at=when) for pnl, when in ledger]

        with _paper_service_with(row, trades):
            overview = _run_coroutine(
                service.get_portfolio_overview(USER, environment="paper")
            )

        # ── The figures the service carries verbatim. Sign of zero included.
        carried = {
            "total_equity": total_equity,
            "total_value": total_equity,
            "available_balance": available_balance,
            "free_balance": available_balance,
            "used_balance": locked_balance,
            "unrealized_pnl": unrealized_pnl,
        }
        for key, expected in carried.items():
            assert _identical_float(overview[key], expected), (
                f"{key} was READ from the paper row and must be reported unchanged: "
                f"{_describe(overview[key], expected)}. The row was complete, so nothing here "
                f"was absent."
            )
            if expected == 0.0:
                census.mark("zero_figure")
                if math.copysign(1.0, expected) < 0:
                    census.mark("negative_zero_figure")
            if expected == FABRICATED_CAPITAL:
                census.mark("genuine_starting_capital")
        census.mark("row")

        # ── The derived sum, by value: IEEE 754 owns the sign of 0.0 + -0.0, not this spec.
        assert overview["cumulative_pnl"] == realized_pnl + unrealized_pnl, (
            "cumulative_pnl is the sum of two figures that were both read, so it was read"
        )

        # ── The lifetime realised figure over the ledger, including the empty one.
        lifetime = overview["realized_pnl"]
        assert lifetime is not None, (
            "every fill's realised figure in this ledger is a readable number, so the lifetime "
            f"sum was read; got None (ledger of {len(trades)})"
        )
        assert lifetime == sum(pnl for pnl, _ in ledger), (
            f"the lifetime realised figure must be the sum of the fills that were read: "
            f"observed {lifetime!r} over {[pnl for pnl, _ in ledger]}"
        )
        if not trades:
            assert lifetime == 0.0, (
                "an empty ledger was READ successfully - nothing has been realised, which is "
                "0.0, not absent. This is the case a one-directional fix gets wrong."
            )
            census.mark("empty_ledger")

        # ── The envelope survives too: nulling figures must not null facts about the request.
        assert overview["environment"] == "paper"
        assert overview["currency"] == "USD"
        assert overview["updated_at"]

        census.finish()

    with publish_hypothesis_statistics(request.node):
        check()
    census.assert_not_vacuous()


def test_a_paper_ledger_reports_absent_only_when_a_fill_was_unreadable(request):
    """Both directions against the real service, on the one field that already has them.

    ``realized_pnl`` (BC-5, ``:920-926``) is the convention wave 1 will spread: ``None`` when any
    fill's realised figure is unreadable - dropping it would publish the sum of a DIFFERENT set of
    fills under the same name - and ``0.0`` for a ledger that was read and nets to zero or holds no
    rows at all. So the biconditional is checkable against the composer's own subject today, with
    no ``xfail``, and this is the honest pin of direction 2 on the real service.

    Direction 1 and direction 2 are asserted on the SAME response: the unreadable fill nulls the
    lifetime figure while every balance on the row - which WAS read - stays exact. A fix that
    nulls the neighbours when one fill is bad fails here, and that is the specific mistake this
    property exists to catch.

    The unreadable fill is dated before today's cutoff on purpose. ``today_realized_pnl``
    (``:911-915``) coerces with a bare ``float()`` behind a date filter, so a same-day unreadable
    figure raises and the paper branch's ``except`` fabricates the whole overview instead. That is
    a real finding, not something to generate around: it is pinned separately, as the fixed-state
    expectation, in :func:`test_an_unreadable_same_day_fill_should_not_fabricate_the_whole_overview`.
    """
    census = Recorder(
        "ledger biconditional",
        floors={
            # Both limbs run on every example, against the same row - see below.
            "readable_ledger": COMPOSER_EXAMPLES,
            "unreadable_ledger": COMPOSER_EXAMPLES,
            "told_apart": COMPOSER_EXAMPLES,
            "zero_sum_ledger": COMPOSER_EXAMPLES // 5,
            "balances_intact": COMPOSER_EXAMPLES * 2,
        },
        labels={
            "readable_ledger": "a fully readable ledger reported its sum",
            "unreadable_ledger": "a ledger with an unreadable fill reported absent",
            "told_apart": "the same row reported a sum when read and absent when not",
            "zero_sum_ledger": "a readable ledger netting to 0.0 reported 0.0",
            "balances_intact": "the balances stayed exact in both limbs",
        },
    )

    service = DashboardAggregationService()

    def _lifetime_and_balances(
        row: Dict[str, Any], trades: List[Dict[str, Any]]
    ) -> Tuple[Any, Any, Any]:
        with _paper_service_with(row, trades):
            overview = _run_coroutine(
                service.get_portfolio_overview(USER, environment="paper")
            )
        return (
            overview["realized_pnl"],
            overview["total_equity"],
            overview["available_balance"],
        )

    @given(
        total_equity=money(),
        available_balance=money(),
        # `just([])` as its own branch: a ledger with no rows at all is the "read fine, nothing
        # realised" case, and it must report 0.0 rather than absent.
        readable_pnls=st.one_of(st.just([]), st.lists(money(), min_size=1, max_size=3)),
        spoiler=st.sampled_from(NOT_A_NUMBER),
    )
    @COMPOSER_SETTINGS
    def check(
        total_equity: float,
        available_balance: float,
        readable_pnls: List[float],
        spoiler: Any,
    ) -> None:
        census.start()

        row = _paper_row(
            total_equity=total_equity,
            available_balance=available_balance,
            locked_balance=0.0,
            unrealized_pnl=0.0,
            realized_pnl=0.0,
            initial_capital=4000.0,
        )
        readable = [_fill(pnl, executed_at=YESTERDAY) for pnl in readable_pnls]
        spoiled = readable + [_fill(spoiler, executed_at=YESTERDAY)]

        # BOTH limbs, on the SAME row, in every example. Drawing one of the two per example would
        # leave the comparison that matters - the two answers differing - to luck; running both
        # makes it an assertion.
        read_lifetime, read_equity, read_available = _lifetime_and_balances(row, readable)
        absent_lifetime, absent_equity, absent_available = _lifetime_and_balances(row, spoiled)

        # ── Direction 1: a ledger that was read reports its sum, and an empty one reports 0.0.
        assert read_lifetime is not None, (
            "every fill is readable, so the lifetime figure was read; got None"
        )
        assert read_lifetime == sum(readable_pnls), _describe(
            read_lifetime, sum(readable_pnls)
        )
        census.mark("readable_ledger")
        if not readable_pnls:
            assert read_lifetime == 0.0, (
                "a ledger with no rows was READ successfully - nothing realised is 0.0, not absent"
            )
            census.mark("zero_sum_ledger")

        # ── Direction 2: add one unreadable fill to that same ledger and the figure goes absent.
        assert absent_lifetime is None, (
            f"one fill carries {spoiler!r}, which is not a number, so the lifetime realised "
            f"figure was NOT read; reporting {absent_lifetime!r} publishes the sum of a different "
            f"set of fills under this name"
        )
        census.mark("unreadable_ledger")

        assert (read_lifetime is None) != (absent_lifetime is None), (
            "the same account row reported the same kind of answer whether its ledger was "
            "readable or not; a trader cannot tell a realised figure from a missing one"
        )
        census.mark("told_apart")

        # ── Direction 1 again, on both responses: what WAS read is untouched by what was not.
        for equity, available in ((read_equity, read_available), (absent_equity, absent_available)):
            assert _identical_float(equity, total_equity), _describe(equity, total_equity)
            assert _identical_float(available, available_balance), _describe(
                available, available_balance
            )
            census.mark("balances_intact")

        census.finish()

    with publish_hypothesis_statistics(request.node):
        check()
    census.assert_not_vacuous()


# ══════════════════════════════════════════════════════════════════════════
# 3. `get_equity_curve` (:1315-1345) - DIRECTION 1 WITH ROWS, DIRECTION 2 LIVE
# ══════════════════════════════════════════════════════════════════════════


def test_an_equity_series_that_was_read_is_reported_point_for_point(request):
    """Direction 1 over generated QuestDB responses, both environments.

    ``get_equity_curve`` returns ``dict(zip(cols, row))`` per row (``:1332-1334``) - no coercion,
    no filtering - so a series that was read is reported verbatim, and this property pins that
    against the real service. Length, order and every equity value including ``0.0`` and ``-0.0``.

    Both environments in one property because the paper branch's synthesis at ``:1338-1344`` is
    reached only when the dataset is empty. With rows the two branches must agree, and a fix to the
    empty case that also touched the non-empty one would show up here as a paper-only difference.

    A series containing ``0.0`` is the interesting one: a wiped-out account's equity curve genuinely
    passes through zero, and those points are measurements.
    """
    census = Recorder(
        "equity series round-trip",
        floors={
            "series": EXAMPLES,
            "zero_point": EXAMPLES * 2,
            "negative_zero_point": EXAMPLES,
            "starting_capital_point": EXAMPLES,
            "four_point_series": EXAMPLES // 4,
        },
        labels={
            "series": "a read series was reported point for point",
            "zero_point": "a genuine 0.0 equity point survived",
            "negative_zero_point": "a genuine -0.0 equity point survived with its sign",
            "starting_capital_point": "a genuine 100000.0 equity point survived",
            "four_point_series": "the series was the forced spine and nothing else",
        },
    )

    service = DashboardAggregationService()

    @given(
        free=st.lists(money(), min_size=0, max_size=3),
        order=arrangement(),
        environment=st.sampled_from(("paper", "live")),
        days=window(),
    )
    @PROPERTY_SETTINGS
    def check(
        free: List[float], order: List[int], environment: str, days: int
    ) -> None:
        census.start()

        # The spine again: every generated series carries 0.0, -0.0 and a genuine 100000.0 in a
        # generated arrangement, with the freely drawn points appended. An equity curve genuinely
        # passes through zero when an account is wiped out, and those points are measurements.
        equities = list(_arrange(order, free[0] if free else 4200.0)) + free[1:]

        questdb = _QuestDBWithRows(_equity_response(equities, days=days))
        with patch.object(
            DashboardAggregationService, "_get_telemetry", return_value=questdb
        ):
            curve = _run_coroutine(
                service.get_equity_curve(USER, days=days, environment=environment)
            )

        assert questdb.queries, "the property must have actually reached QuestDB"
        assert len(curve) == len(equities), (
            f"{environment}: {len(equities)} equity row(s) were read, {len(curve)} reported"
        )
        for index, (point, expected) in enumerate(zip(curve, equities)):
            assert _identical_float(point.get("equity"), expected), (
                f"{environment}: equity point {index} was read and must be reported unchanged: "
                f"{_describe(point.get('equity'), expected)}"
            )
            assert point.get("timestamp"), f"point {index} lost its timestamp"
            if expected == 0.0:
                census.mark("zero_point")
                if math.copysign(1.0, expected) < 0:
                    census.mark("negative_zero_point")
            if expected == FABRICATED_CAPITAL:
                census.mark("starting_capital_point")

        census.mark("series")
        if len(equities) == len(FORCED_SPINE) + 1:
            census.mark("four_point_series")

        census.finish()

    with publish_hypothesis_statistics(request.node):
        check()
    census.assert_not_vacuous()


def test_a_live_equity_read_that_found_nothing_reports_nothing(request):
    """Direction 2 against the real service, on the limb that already gets it right.

    The live branch answers ``[]`` for both absent cases - a successful read with no rows, and a
    read that raised - and returns ``[]`` at ``:1345`` without synthesising. So direction 2 is
    hard-assertable here today, and asserting it is what makes the paper expectation in
    :func:`test_a_paper_equity_read_that_found_nothing_should_report_nothing` visibly this
    codebase's own convention applied consistently, rather than a preference this spec invented.

    Both absent cases are generated, not just the empty one, because they are different code paths:
    the empty dataset falls through the ``if result and result.get("dataset")`` guard at ``:1332``,
    while the raise is caught by the ``except`` at ``:1335``.
    """
    census = Recorder(
        "live absent equity",
        floors={"empty_read": EXAMPLES, "failed_read": EXAMPLES},
        labels={
            "empty_read": "a successful but empty live read reported []",
            "failed_read": "a failed live read reported []",
        },
    )

    service = DashboardAggregationService()

    @given(days=window())
    @PROPERTY_SETTINGS
    def check(days: int) -> None:
        census.start()

        # Both absent cases in every example. Drawing one per example would make the census a
        # statement about a coin flip, and with a four-member window the whole input space would
        # be exhausted in eight examples.
        for bucket, questdb in (
            ("empty_read", _EmptyQuestDB()),
            ("failed_read", _QuestDBUnreachable()),
        ):
            with patch.object(
                DashboardAggregationService, "_get_telemetry", return_value=questdb
            ):
                curve = _run_coroutine(
                    service.get_equity_curve(USER, days=days, environment="live")
                )

            assert questdb.queries, "the property must have actually reached QuestDB"
            assert curve == [], (
                f"no equity rows were read over {days} day(s) "
                f"({'the read raised' if bucket == 'failed_read' else 'the read was empty'}), so "
                f"the series is empty; got {len(curve)} synthesised point(s): {curve}"
            )
            census.mark(bucket)

        census.finish()

    with publish_hypothesis_statistics(request.node):
        check()
    census.assert_not_vacuous()


# ══════════════════════════════════════════════════════════════════════════
# 4. `get_dashboard_data` (:1798-1815) - DIRECTION 1 THROUGH THE COMPOSER
# ══════════════════════════════════════════════════════════════════════════


@requires_routers
def test_the_composer_publishes_every_figure_the_portfolio_read_reported(request):
    """Direction 1 through the real composer: a successful portfolio survives the money block.

    The subject is the dict literal at ``:1798-1815``, one ``float()`` per key. Task 6.1 retypes
    every one of them, and the failure mode of that edit is a figure that changes on the way out -
    a ``-0.0`` normalised, a ``0.0`` turned into ``None`` by a coercion written as
    ``value or None``, a key silently falling through to its ``.get`` default because the retyped
    expression misspelled it. All three are caught here.

    ``get_portfolio_overview`` is patched to SUCCEED and return the generated portfolio, which is
    the only way to isolate the composer: the paper branch catches its own failures (``:948``), so
    what reaches the money block is always a dict and the question is purely what the block does to
    it. The generated portfolio is a real reading of an account, and every key the block reads is
    present in it - so on every example the ``.get`` defaults at ``:1799-1812`` are dead code, and
    a figure that comes back equal to one of those defaults means a key was dropped.

    ``OVERVIEW_MONEY_KEYS`` has exactly four members and :data:`FORCED_SPINE` has three, so every
    example arranges ``0.0``, ``-0.0`` and a genuine ``100000.0`` across the money block alongside
    one freely generated figure, in a generated order. The wholly-empty account - four zeros, no
    free parameters - is checked once per environment after the generated pass rather than drawn
    from a boolean, because a boolean only decides how many generated examples it displaces.
    """
    census = Recorder(
        "composer round-trip",
        floors={
            "portfolio": COMPOSER_EXAMPLES,
            # `OVERVIEW_MONEY_KEYS` has exactly four members and the spine has three, so every
            # example arranges 0.0, -0.0 and a genuine 100000.0 across the money block plus one
            # freely generated figure. The `all_zero` examples contribute four zeros instead.
            "zero_figure": COMPOSER_EXAMPLES * 2,
            "negative_zero_figure": COMPOSER_EXAMPLES,
            "genuine_starting_capital": COMPOSER_EXAMPLES,
            # Once per environment, deterministically - see below.
            "all_zero_portfolio": 2,
        },
        labels={
            "portfolio": "a successful portfolio survived the composer",
            "zero_figure": "a genuine 0.0 survived the composer",
            "negative_zero_figure": "a genuine -0.0 survived the composer with its sign",
            "genuine_starting_capital": "a genuine 100000.0 survived the composer",
            "all_zero_portfolio": "a wholly empty-but-read account stayed all zeros",
        },
    )

    service = DashboardAggregationService()

    def _compose(environment: str, expected: Dict[str, float]) -> Dict[str, Any]:
        portfolio = dict(_genuine_zero_portfolio(environment))
        portfolio.update(expected)
        with _portfolio_overview_returning(portfolio), _no_cached_balances(), patch.object(
            DashboardAggregationService, "_get_telemetry", return_value=_EmptyQuestDB()
        ):
            return _run_coroutine(
                service.get_dashboard_data(USER, equity_days=30, environment=environment)
            )

    @given(
        order=arrangement(),
        free=money(),
        environment=st.sampled_from(("paper", "live")),
    )
    @COMPOSER_SETTINGS
    def check(order: List[int], free: float, environment: str) -> None:
        census.start()

        expected = dict(zip(OVERVIEW_MONEY_KEYS, _arrange(order, free)))
        data = _compose(environment, expected)

        overview = data["overview"]
        for key, value in expected.items():
            assert _identical_float(overview[key], value), (
                f"{environment}: {key} was READ as {value!r} and the composer published "
                f"{overview[key]!r}. {_describe(overview[key], value)}"
            )
            if value == 0.0:
                census.mark("zero_figure")
                if math.copysign(1.0, value) < 0:
                    census.mark("negative_zero_figure")
            if value == FABRICATED_CAPITAL:
                census.mark("genuine_starting_capital")

        census.mark("portfolio")

        assert data["environment"] == environment
        assert overview["currency"] == _genuine_zero_portfolio(environment)["currency"]

        census.finish()

    with publish_hypothesis_statistics(request.node):
        check()

    # ── The control for wave 1's own failure mode, checked once per environment rather than
    #    generated. An account read successfully and found wholly empty has no free parameters -
    #    it is one payload, `dict.fromkeys(OVERVIEW_MONEY_KEYS, 0.0)` - so drawing it from a
    #    boolean would only decide how many of the generated examples it displaced, which is what
    #    starved the spine when it was generated. Four zeros in, four zeros out, and
    #    `_fabricated_capital_sites` confirms the composer reached for no default on the way.
    #    No `census.start()` / `finish()` here: `finish` emits Hypothesis ``event`` labels, which
    #    only mean anything inside a generating context. `mark` is just the count.
    for environment in ("paper", "live"):
        empty_account = dict.fromkeys(OVERVIEW_MONEY_KEYS, 0.0)
        overview = _compose(environment, empty_account)["overview"]
        assert {key: overview[key] for key in OVERVIEW_MONEY_KEYS} == empty_account, (
            f"{environment}: a successful read of an empty account must report 0.0 for all four "
            f"figures, not None; got {[overview[key] for key in OVERVIEW_MONEY_KEYS]}"
        )
        assert _fabricated_capital_sites(overview) == [], (
            f"{environment}: the portfolio read succeeded and reported zeros, so nothing in the "
            f"overview should carry {FABRICATED_CAPITAL}: {_fabricated_capital_sites(overview)}"
        )
        census.mark("all_zero_portfolio")

    census.assert_not_vacuous()


# ══════════════════════════════════════════════════════════════════════════
# 5. THE FIXED-STATE EXPECTATIONS - DIRECTION 2 WHERE THE `float()` GATE REFUSES IT
# ══════════════════════════════════════════════════════════════════════════
#
# Three properties that state direction 2 against subjects that cannot satisfy it on `F`. Each is
# marked `xfail` NON-STRICT, and the reason string names the line that refuses it. See the module
# docstring for why non-strict: this file must be green on `F` and on `F'`, and a strict marker
# would make wave 1's success a red run here. The strict "this must change" claims are
# `tests/test_dashboard_absent_figures.py`'s, where an XFAIL is the deliverable.
#
# These are not decorative. The body runs on every example, so a NEW failure mode - a hang, a
# different exception, a fabricated figure somewhere direction 1 does not reach - still surfaces in
# the run; and when wave 1 lands, each one flips to XPASS and the marker comes off with the fix.


@requires_routers
@pytest.mark.xfail(
    strict=False,
    reason=(
        "fixed-state expectation (task 6.1). A None money figure cannot cross the composer's "
        "money block today: float() at dashboard_aggregation_service.py:1798-1815 raises "
        "TypeError, and the outer except re-raises at :1889-1890, so the whole dashboard 500s. "
        "Measured in tests/test_dashboard_absent_figures.py section 5."
    ),
)
def test_the_composer_should_publish_absent_for_a_figure_that_was_not_read(request):
    """Direction 2 through the composer: a portfolio whose money was not read publishes ``None``.

    Generated over WHICH subset of the four figures is absent, not just the all-absent case. A
    partial outage is the realistic one - a balance endpoint that answered while an equity endpoint
    did not - and it is also the case that separates a real fix from a blanket one: the figures in
    the same response that WERE read must still come back exact, which is asserted here on the same
    payload. A fix that nulls the whole block when any one figure is absent fails this even after
    the ``TypeError`` is gone.

    ``realized_pnl`` is the shape being asked for. It already crosses this block as ``None`` via
    ``_finite_float`` at ``:1810``, so the target is not hypothetical - it is the neighbouring line.
    """
    census = Recorder(
        "composer absent",
        floors={"partial": 1, "total": 1},
        labels={
            "partial": "some figures absent, the rest still exact",
            "total": "all four figures absent",
        },
    )

    service = DashboardAggregationService()

    @given(
        absent=st.lists(
            st.sampled_from(OVERVIEW_MONEY_KEYS), min_size=1, max_size=4, unique=True
        ),
        present_value=money(),
        environment=st.sampled_from(("paper", "live")),
    )
    @COMPOSER_SETTINGS
    def check(absent: List[str], present_value: float, environment: str) -> None:
        census.start()

        portfolio = dict(_genuine_zero_portfolio(environment))
        for key in OVERVIEW_MONEY_KEYS:
            portfolio[key] = None if key in absent else present_value

        with _portfolio_overview_returning(portfolio), _no_cached_balances(), patch.object(
            DashboardAggregationService, "_get_telemetry", return_value=_EmptyQuestDB()
        ):
            data = _run_coroutine(
                service.get_dashboard_data(USER, equity_days=30, environment=environment)
            )

        overview = data["overview"]
        for key in OVERVIEW_MONEY_KEYS:
            if key in absent:
                assert overview[key] is None, (
                    f"{environment}: {key} was not read, so it must be absent; got "
                    f"{overview[key]!r}"
                )
            else:
                assert _identical_float(overview[key], present_value), (
                    f"{environment}: {key} WAS read while its neighbours were not, and must "
                    f"survive: {_describe(overview[key], present_value)}"
                )

        census.mark("total" if len(absent) == len(OVERVIEW_MONEY_KEYS) else "partial")
        census.finish()

    with publish_hypothesis_statistics(request.node):
        check()
    census.assert_not_vacuous()


@pytest.mark.xfail(
    strict=False,
    reason=(
        "fixed-state expectation (task 6.x). dashboard_aggregation_service.py:1338-1344 "
        "synthesises a two-point flat curve at the paper starting capital when no equity rows "
        "were read. The live limb at :1345 already returns []."
    ),
)
def test_a_paper_equity_read_that_found_nothing_should_report_nothing(request):
    """Direction 2 on the paper equity limb, generated over both absent cases and the window.

    ``days`` is generated because the synthesis HONOURS it - the fabricated pair spans exactly the
    window the caller asked for, so a 90-day request renders as ninety days of measured break-even
    performance. The lie is shaped to be convincing, and generating the window is what shows that
    the shape is not incidental.

    The hard-asserted counterpart is :func:`test_a_live_equity_read_that_found_nothing_reports_nothing`
    - identical premise, identical expectation, live.
    """
    census = Recorder(
        "paper absent equity",
        floors={"empty_read": 1, "failed_read": 1},
        labels={
            "empty_read": "a successful but empty paper read",
            "failed_read": "a failed paper read",
        },
    )

    service = DashboardAggregationService()

    @given(failed=st.booleans(), days=st.sampled_from((1, 7, 30, 90)))
    @PROPERTY_SETTINGS
    def check(failed: bool, days: int) -> None:
        census.start()

        questdb = _QuestDBUnreachable() if failed else _EmptyQuestDB()
        with patch.object(
            DashboardAggregationService, "_get_telemetry", return_value=questdb
        ):
            curve = _run_coroutine(
                service.get_equity_curve(USER, days=days, environment="paper")
            )

        assert curve == [], (
            f"no paper equity rows were read over {days} day(s) "
            f"({'the read raised' if failed else 'the read was empty'}), so the series is empty; "
            f"got {len(curve)} synthesised point(s) at "
            f"{[point.get('equity') for point in curve]}"
        )
        census.mark("failed_read" if failed else "empty_read")

        census.finish()

    with publish_hypothesis_statistics(request.node):
        check()
    census.assert_not_vacuous()


@pytest.mark.xfail(
    strict=False,
    reason=(
        "fixed-state expectation (task 6.x). today_realized_pnl at "
        "dashboard_aggregation_service.py:911-915 coerces each same-day fill with a bare float(), "
        "so one unreadable figure raises and the paper except at :948-975 replaces the ENTIRE "
        "overview with the starting capital. Found by this property; not covered by "
        "tests/test_dashboard_absent_figures.py, whose section 4 case is an absent column."
    ),
)
def test_an_unreadable_same_day_fill_should_not_fabricate_the_whole_overview(request):
    """Direction 2, and the finding this file turned up: one bad fill loses every balance.

    A same-day fill whose realised figure is unreadable makes ``float()`` raise inside the paper
    branch's ``try``, and the ``except`` answers with four fabricated figures at the starting
    capital - so a single malformed ledger row does not degrade one field, it replaces the account.
    The row's balances were read perfectly well and must survive; only the figure that could not be
    read is absent.

    This is the same defect as the composer's, one layer down, and it is why direction 2 cannot be
    expressed as "the response contains a ``None`` somewhere": the failure here is that the response
    contains no ``None`` at all, just numbers nobody measured.
    """
    census = Recorder(
        "same-day unreadable fill",
        floors={"spoiled": 1},
        labels={"spoiled": "a same-day fill carried an unreadable realised figure"},
    )

    service = DashboardAggregationService()

    @given(
        total_equity=money(),
        available_balance=money(),
        spoiler=st.sampled_from(NOT_A_NUMBER),
    )
    @COMPOSER_SETTINGS
    def check(total_equity: float, available_balance: float, spoiler: Any) -> None:
        census.start()

        row = _paper_row(
            total_equity=total_equity,
            available_balance=available_balance,
            locked_balance=0.0,
            unrealized_pnl=0.0,
            realized_pnl=0.0,
            initial_capital=4000.0,
        )
        trades = [_fill(spoiler, executed_at=TODAY)]

        with _paper_service_with(row, trades):
            overview = _run_coroutine(
                service.get_portfolio_overview(USER, environment="paper")
            )

        assert _identical_float(overview["total_equity"], total_equity), (
            f"the account row was read fine; one unreadable same-day fill must not replace the "
            f"balance. {_describe(overview['total_equity'], total_equity)}"
        )
        assert _identical_float(overview["available_balance"], available_balance), _describe(
            overview["available_balance"], available_balance
        )
        assert overview["realized_pnl"] is None, (
            f"the fill carries {spoiler!r}, so today's realised figure was not read"
        )
        census.mark("spoiled")

        census.finish()

    with publish_hypothesis_statistics(request.node):
        check()
    census.assert_not_vacuous()
