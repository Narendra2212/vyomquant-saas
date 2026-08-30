"""Property tests for Subscription period arithmetic.

Feature: marketplace-subscriptions-paper-trading
Design reference: ``design.md § Property-to-test mapping`` ->
``P-12, P-14 | tests/property/test_subscription_period.py | utc_instants() |
dateutil.relativedelta(months=+1) as an independent second implementation``.

Module under test: ``backend_app/backend/marketplace/subscription_period.py`` (task 4.7).

Properties living here
----------------------
``test_p12_calendar_month_clamps_day_and_preserves_clock_time``  (task 4.8, Requirement 11.4)
``test_p14_renewal_expiry_is_strictly_increasing``               (task 4.9, Requirement 11.5)

WHY ``relativedelta`` IS THE ORACLE
-----------------------------------
``add_one_calendar_month`` rolls the month itself and clamps the day with
``calendar.monthrange``. Re-deriving the expected value the same way in the test would only
restate the implementation, so the comparison is made against ``dateutil.relativedelta``,
a separately written and widely exercised implementation of the same calendar rule
(``+1 month`` keeps the clock time and clamps the day to the target month's length). A
disagreement therefore points at one of the two implementations rather than at a shared
misreading of the calendar.

``python-dateutil`` is a transitive dependency already resolved in this environment
(``pandas`` requires it); it is imported at module scope so a missing oracle is a hard
collection error rather than a silently weakened test.
"""

from __future__ import annotations

import calendar
from datetime import datetime, timezone
from typing import Tuple

from dateutil.relativedelta import relativedelta
from hypothesis import HealthCheck, given, settings

from backend_app.backend.marketplace.subscription_period import (
    add_one_calendar_month,
    period_for_renewal,
)
from tests.strategies.marketplace_generators import utc_instants

#: The configuration ``design.md § Property-based testing configuration`` prescribes for
#: every property test in this plan: at least 100 examples, no per-example deadline (the
#: first example pays import cost), and ``derandomize`` left at its default False so the
#: ``.hypothesis`` database of failing examples keeps accumulating across runs.
PROPERTY_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


def _following_month(t: datetime) -> Tuple[int, int]:
    """Return the ``(year, month)`` immediately after ``t``'s month.

    Stated independently of the module under test so the "falls in the month following
    ``t``" claim of P-12 is checked rather than assumed.
    """
    if t.month == 12:
        return t.year + 1, 1
    return t.year, t.month + 1


# Feature: marketplace-subscriptions-paper-trading, Property 12 (round-trip, calendar
# month): For all UTC instants t and the period function m: m(t) falls in the calendar
# month following the month of t, has the same clock time as t, and has day-of-month equal
# to min(day_of_month(t), last_day(target_month)).
@PROPERTY_SETTINGS
@given(t=utc_instants())
def test_p12_calendar_month_clamps_day_and_preserves_clock_time(t: datetime) -> None:
    """`add_one_calendar_month` rolls the month, keeps the clock, clamps the day.

    **Validates: Requirements 11.4**
    """
    rolled = add_one_calendar_month(t)

    # Claim 1 - the result is in the calendar month immediately following t's month.
    expected_year, expected_month = _following_month(t)
    assert (rolled.year, rolled.month) == (expected_year, expected_month), (
        f"{t.isoformat()} rolled to {rolled.isoformat()}, which is not in "
        f"{expected_year}-{expected_month:02d}"
    )

    # Claim 2 - the clock time is untouched, and the instant stays on the UTC calendar.
    assert (rolled.hour, rolled.minute, rolled.second, rolled.microsecond) == (
        t.hour,
        t.minute,
        t.second,
        t.microsecond,
    ), f"clock time changed: {t.isoformat()} -> {rolled.isoformat()}"
    assert rolled.tzinfo is timezone.utc

    # Claim 3 - the day-of-month is clamped to the target month's last day.
    last_day = calendar.monthrange(expected_year, expected_month)[1]
    assert rolled.day == min(t.day, last_day), (
        f"{t.isoformat()} rolled to day {rolled.day}; expected "
        f"min({t.day}, {last_day}) = {min(t.day, last_day)}"
    )

    # All three claims at once, against an independently written implementation of the
    # same calendar rule.
    assert rolled == t + relativedelta(months=+1), (
        f"disagrees with dateutil: add_one_calendar_month({t.isoformat()}) = "
        f"{rolled.isoformat()}, relativedelta gives "
        f"{(t + relativedelta(months=+1)).isoformat()}"
    )


# Feature: marketplace-subscriptions-paper-trading, Property 14 (metamorphic, renewal
# monotonicity): For all Subscriptions and all confirmed renewal payments: the new expiry is
# strictly greater than the previous expiry, and is one calendar month after the later of the
# previous expiry and the confirmation instant.
@PROPERTY_SETTINGS
@given(current_expiry=utc_instants(), confirmation_instant=utc_instants())
def test_p14_renewal_expiry_is_strictly_increasing(
    current_expiry: datetime, confirmation_instant: datetime
) -> None:
    """`period_for_renewal` extends the period, never shortens it.

    The two instants are drawn independently, so both admissible orderings are covered: an
    early renewal (``confirmation_instant < current_expiry``, where the unexpired remainder
    must be carried forward) and a late one (``confirmation_instant >= current_expiry``,
    where the new period runs from the payment).

    **Validates: Requirements 11.5**
    """
    renewed = period_for_renewal(current_expiry, confirmation_instant)

    # Claim 1 - one calendar month after the later of the two instants, checked against the
    # independently written oracle rather than against add_one_calendar_month.
    anchor = max(current_expiry, confirmation_instant)
    assert renewed == anchor + relativedelta(months=+1), (
        f"renewal of expiry={current_expiry.isoformat()} at "
        f"{confirmation_instant.isoformat()} gave {renewed.isoformat()}; dateutil gives "
        f"{(anchor + relativedelta(months=+1)).isoformat()} from anchor "
        f"{anchor.isoformat()}"
    )

    # Claim 2 - the new expiry is strictly later than the previous one, whichever instant
    # won the maximum. This is the invariant that makes an early renewal an extension.
    assert renewed > current_expiry, (
        f"renewal did not extend the period: {current_expiry.isoformat()} -> "
        f"{renewed.isoformat()} (confirmed at {confirmation_instant.isoformat()})"
    )
    assert renewed.tzinfo is timezone.utc

    # Claim 3 - the metamorphic relation iterated: renewing again from the expiry just
    # computed, with the same confirmation instant, is strictly increasing once more, so a
    # sequence of renewals can never walk the expiry backwards.
    renewed_twice = period_for_renewal(renewed, confirmation_instant)
    assert renewed_twice > renewed, (
        f"a second renewal did not extend the period: {renewed.isoformat()} -> "
        f"{renewed_twice.isoformat()}"
    )
