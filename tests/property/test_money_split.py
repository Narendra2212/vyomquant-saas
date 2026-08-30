"""Property tests for the 90/10 revenue split.

Feature: marketplace-subscriptions-paper-trading
Design reference: ``design.md § Property-to-test mapping`` ->
``P-1 … P-4 | tests/property/test_money_split.py | minor_amounts(), ordered pairs |
pure arithmetic restated independently: owner_share == (a*90)//100 checked against
a*90 - 100*owner_share in [0,100)``.

Module under test: ``backend_app/backend/marketplace/money.py`` (task 4.1).

Properties living here
----------------------
``test_p1_conservation``                                     (task 4.2, Requirements 10.1, 10.2)
``test_p2_share_bounds``                                     (task 4.3, Requirements 10.1, 10.5)
``test_p3_largest_whole_unit_not_exceeding_ninety_percent``  (task 4.4, Requirements 10.1, 10.2)
``test_p4_split_is_monotonic``                               (task 4.5, Requirement 10.1)

Tasks 4.3 … 4.5 append their functions below; the shared ``PROPERTY_SETTINGS`` and the
``_remainder`` oracle helper are written once here for all four.

WHY THE ORACLE IS A REMAINDER RANGE AND NOT ``(a * 90) // 100``
--------------------------------------------------------------
Restating ``(a * 90) // 100`` in the test would only re-execute the implementation's own
expression, so a wrong operator on both sides would agree with itself. The oracle used
instead is the defining property of the quotient: ``owner_share`` is the correct
truncated-toward-zero result exactly when the remainder ``a * 90 - 100 * owner_share``
lies in ``[0, 100)``. That is a statement about what the number *is*, written without
``//`` at all, so it is not a paraphrase of the code under test.

WHY THERE IS NO ``float`` AND NO ``Decimal`` IN THIS MODULE
----------------------------------------------------------
Requirement 10.3 forbids binary floating-point money arithmetic. Every value the split
touches is a Python ``int``, and Python ``int`` arithmetic is exact at every magnitude, so
the assertions here are written in ``int`` too — comparing against a ``float`` expectation
would introduce the very representation error the requirement exists to exclude, and near
``MAX_AMOUNT_MINOR`` (11 digits, times 90) it would begin to lose precision outright.
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings

from backend_app.backend.marketplace.money import (
    OWNER_SHARE_PERCENT,
    split_ninety_ten,
)
from tests.strategies.marketplace_generators import minor_amounts

#: The configuration ``design.md § Property-based testing configuration`` prescribes for
#: every property test in this plan: at least 100 examples, no per-example deadline (the
#: first example pays import cost), and ``derandomize`` left at its default False so the
#: ``.hypothesis`` database of failing examples keeps accumulating across runs.
PROPERTY_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


def _remainder(amount_minor: int, owner_share: int) -> int:
    """Return ``amount_minor * 90 - 100 * owner_share``, the split's exact remainder.

    ``owner_share`` is the truncated-toward-zero ninety percent of ``amount_minor`` if and
    only if this value lies in ``[0, 100)`` — the design's stated oracle for P-1 … P-4,
    written with multiplication and subtraction only so it shares no operator with the
    implementation's ``//``.
    """
    return amount_minor * OWNER_SHARE_PERCENT - 100 * owner_share


# Feature: marketplace-subscriptions-paper-trading, Property 1 (invariant, conservation):
# For all integer payment amounts a in Minor_Units with a >= 0:
# owner_share(a) + platform_fee(a) == a.
@PROPERTY_SETTINGS
@given(amount_minor=minor_amounts())
def test_p1_conservation(amount_minor: int) -> None:
    """The two shares sum back to the amount, with no remainder unaccounted for.

    **Validates: Requirements 10.1, 10.2**
    """
    owner_share, platform_fee = split_ninety_ten(amount_minor)

    # The property itself: nothing is created and nothing is lost by the split.
    assert owner_share + platform_fee == amount_minor, (
        f"split_ninety_ten({amount_minor}) = ({owner_share}, {platform_fee}); "
        f"the shares sum to {owner_share + platform_fee}, a discrepancy of "
        f"{amount_minor - owner_share - platform_fee} Minor_Units"
    )

    # Both shares are exact integer Minor_Unit counts. A Decimal or a float summing to the
    # amount would still satisfy the equality above while violating Requirement 10.3, so
    # the type is asserted rather than inferred. bool is excluded explicitly, because
    # isinstance(True, int) is True.
    for name, share in (("owner_share", owner_share), ("platform_fee", platform_fee)):
        assert isinstance(share, int) and not isinstance(share, bool), (
            f"{name} must be an int number of Minor_Units, got "
            f"{type(share).__name__}: {share!r}"
        )

    # The conservation above holds for any pair that sums to the amount, including a
    # 50/50 one. The oracle pins which pair it must be: owner_share is the
    # truncated-toward-zero ninety percent exactly when the remainder is in [0, 100).
    remainder = _remainder(amount_minor, owner_share)
    assert 0 <= remainder < 100, (
        f"owner_share {owner_share} is not the truncated 90 percent of {amount_minor}: "
        f"{amount_minor}*{OWNER_SHARE_PERCENT} - 100*{owner_share} = {remainder}, "
        "which is outside [0, 100)"
    )

# Feature: marketplace-subscriptions-paper-trading, Property 2 (invariant, bounds):
# For all a >= 0: 0 <= owner_share(a) <= a and 0 <= platform_fee(a) <= a.
@PROPERTY_SETTINGS
@given(amount_minor=minor_amounts())
def test_p2_share_bounds(amount_minor: int) -> None:
    """Neither share is ever negative, and neither share ever exceeds the amount.

    These are exactly the two inequalities Requirement 10.5 makes the Settlement_Record's
    ``CHECK`` constraints enforce (``owner_share >= 0``, ``platform_fee >= 0``, and, with
    conservation, each bounded above by ``amount``). A split that violated either would be
    refused by the database at write time, so the arithmetic has to guarantee it up front
    rather than discover it as an integrity error mid-transaction.

    **Validates: Requirements 10.1, 10.5**
    """
    owner_share, platform_fee = split_ninety_ten(amount_minor)

    # Lower bounds: a negative share would mean the split invented a debt out of a payment,
    # and it is the case Requirement 10.5's CHECK constraints reject outright.
    for name, share in (("owner_share", owner_share), ("platform_fee", platform_fee)):
        assert share >= 0, (
            f"{name} is negative: split_ninety_ten({amount_minor}) = "
            f"({owner_share}, {platform_fee})"
        )

        # Upper bounds: either share exceeding the amount would mean the split paid out more
        # than was collected. Asserted per-share rather than inferred from the sum, because a
        # (a + 1, -1) pair conserves the total while breaking both bounds.
        assert share <= amount_minor, (
            f"{name} {share} exceeds the amount {amount_minor}: "
            f"split_ninety_ten({amount_minor}) = ({owner_share}, {platform_fee})"
        )


# Feature: marketplace-subscriptions-paper-trading, Property 3 (invariant, ratio bound):
# For all a >= 0: owner_share(a) * 100 <= a * 90 and (owner_share(a) + 1) * 100 > a * 90,
# so the owner receives the largest whole Minor_Unit amount not exceeding 90 percent and the
# rounding remainder never exceeds one Minor_Unit.
@PROPERTY_SETTINGS
@given(amount_minor=minor_amounts())
def test_p3_largest_whole_unit_not_exceeding_ninety_percent(amount_minor: int) -> None:
    """``owner_share`` is the largest whole Minor_Unit count at or below 90 percent.

    The pair of inequalities is what makes "90 percent, truncated" a *unique* answer rather
    than a family of them. P-1 pins that the two shares sum to the amount and P-2 pins that
    neither leaves ``[0, a]``, but both would still admit an owner share one Minor_Unit shy
    of its due — a systematic under-payment that conserves the total by over-crediting the
    platform. The lower inequality forbids the owner share from exceeding 90 percent; the
    upper one forbids it from being low enough that one more whole Minor_Unit would still
    fit. Together they leave exactly one admissible value, and they cap the rounding
    remainder at strictly less than one Minor_Unit (Requirement 10.2: no rounding remainder
    unaccounted for).

    Both sides are scaled by 100 so the comparison stays in ``int``: ``a * 90 / 100`` would
    be a ``float`` and, at the eleven-digit top of Requirement 10.1's domain, would compare
    wrongly against the exact integer share.

    **Validates: Requirements 10.1, 10.2**
    """
    owner_share, platform_fee = split_ninety_ten(amount_minor)

    # Lower inequality: the owner share never exceeds ninety percent of the amount. Written
    # as a cross-multiplied comparison, so no division and no float appears on either side.
    assert owner_share * 100 <= amount_minor * OWNER_SHARE_PERCENT, (
        f"owner_share {owner_share} exceeds 90 percent of {amount_minor}: "
        f"{owner_share}*100 = {owner_share * 100} > "
        f"{amount_minor}*{OWNER_SHARE_PERCENT} = {amount_minor * OWNER_SHARE_PERCENT}"
    )

    # Upper inequality: no larger whole Minor_Unit count would still fit under ninety
    # percent. This is the half that rules out an owner share rounded down too far.
    assert (owner_share + 1) * 100 > amount_minor * OWNER_SHARE_PERCENT, (
        f"owner_share {owner_share} is not the largest whole Minor_Unit count at or below "
        f"90 percent of {amount_minor}: {owner_share + 1} would also fit, since "
        f"{(owner_share + 1)}*100 = {(owner_share + 1) * 100} <= "
        f"{amount_minor}*{OWNER_SHARE_PERCENT} = {amount_minor * OWNER_SHARE_PERCENT}"
    )

    # The two inequalities above bound the remainder; this states where it lands. The
    # platform's share exceeds its exact ten percent by precisely that remainder
    # (``platform_fee*100 - a*10 == a*90 - owner_share*100``), and by less than one whole
    # Minor_Unit — so the truncation is a rounding direction, not a leak.
    remainder = _remainder(amount_minor, owner_share)
    assert platform_fee * 100 - amount_minor * 10 == remainder, (
        f"the remainder is unaccounted for: platform_fee {platform_fee} over its exact ten "
        f"percent is {platform_fee * 100 - amount_minor * 10}/100 Minor_Units, but the "
        f"owner share's shortfall is {remainder}/100"
    )
    assert 0 <= remainder < 100, (
        f"the rounding remainder for {amount_minor} is {remainder}/100 Minor_Units, "
        "outside [0, 1) whole Minor_Units"
    )

# Feature: marketplace-subscriptions-paper-trading, Property 4 (metamorphic, monotonicity):
# For all a1 <= a2: owner_share(a1) <= owner_share(a2) and platform_fee(a1) <= platform_fee(a2).
@PROPERTY_SETTINGS
@given(first_amount_minor=minor_amounts(), second_amount_minor=minor_amounts())
def test_p4_split_is_monotonic(first_amount_minor: int, second_amount_minor: int) -> None:
    """Charging more never pays either party less.

    This is the one property in the file that relates two *different* calls, which is what
    makes it metamorphic: P-1 … P-3 each pin a single split against an oracle, and all three
    would still hold for an implementation whose shares wobbled non-monotonically across
    neighbouring amounts. Monotonicity is what a reader of the ledger assumes without being
    told — that a larger payment credits the owner at least as much as a smaller one, and the
    platform likewise — and it is the assumption a percentage boundary or an off-by-one in the
    truncation would break while every per-amount invariant still passed.

    The two amounts are drawn independently and then ordered, so ``first <= second`` covers
    the strict case, the equal case (which also re-states determinism: the same amount must
    split the same way both times) and adjacent amounts one Minor_Unit apart, where the
    truncation boundary lives.

    Both comparisons are ``int`` comparisons on values ``split_ninety_ten`` returns as
    ``int``; no ratio and no division is formed, so nothing here can round (Requirement 10.3).

    **Validates: Requirements 10.1**
    """
    # The ordered pair the property quantifies over. Sorting rather than filtering keeps every
    # drawn example usable, including first == second.
    lower_amount, higher_amount = sorted((first_amount_minor, second_amount_minor))
    assert lower_amount <= higher_amount  # the property's premise, made explicit

    lower_owner_share, lower_platform_fee = split_ninety_ten(lower_amount)
    higher_owner_share, higher_platform_fee = split_ninety_ten(higher_amount)

    # The owner's half of the property: a larger payment never credits the owner less.
    assert lower_owner_share <= higher_owner_share, (
        f"owner_share is not monotonic: {lower_amount} -> {lower_owner_share} but the larger "
        f"{higher_amount} -> {higher_owner_share}, a drop of "
        f"{lower_owner_share - higher_owner_share} Minor_Units"
    )

    # The platform's half. Asserted separately, because conservation ties the two shares
    # together: an owner share that grew too fast would hold the sum while shrinking the fee,
    # and P-1 alone would not notice.
    assert lower_platform_fee <= higher_platform_fee, (
        f"platform_fee is not monotonic: {lower_amount} -> {lower_platform_fee} but the "
        f"larger {higher_amount} -> {higher_platform_fee}, a drop of "
        f"{lower_platform_fee - higher_platform_fee} Minor_Units"
    )

    # Monotonicity alone admits a split that never moves at all. Pinning the increase to the
    # amount's own increase is what keeps both shares tracking the payment: the two growths
    # sum to exactly the extra amount charged, so no part of an increase is dropped and none
    # is duplicated between the parties.
    amount_increase = higher_amount - lower_amount
    owner_increase = higher_owner_share - lower_owner_share
    fee_increase = higher_platform_fee - lower_platform_fee
    assert owner_increase + fee_increase == amount_increase, (
        f"the {amount_increase} extra Minor_Units between {lower_amount} and "
        f"{higher_amount} are not distributed: owner gained {owner_increase} and platform "
        f"gained {fee_increase}, totalling {owner_increase + fee_increase}"
    )
